"""The advantages block's ordering, reorder, and panel-mapping behaviour."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QSpinBox

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power
from mm_companion.ui.sections.advantages import (
    SORT_MANUAL,
    SORT_NAME,
    SORT_RANK,
    SORT_TYPE,
    AdvantagesSection,
    name_max_width,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _section(selections: list[AdvantageSelection]) -> AdvantagesSection:
    data = load_game_data()
    char = Character.new_default(data)
    char.advantages = list(selections)
    return AdvantagesSection(data, char)


def _names(section: AdvantagesSection) -> list[str]:
    return [s.name for s in section._character.advantages]


def _pick(section: AdvantagesSection, name: str) -> None:
    """Select *name* in the picker combo (fires the parameter/rank sync)."""
    index = next(
        i
        for i in range(section._advantage_combo.count())
        if section._advantage_combo.itemData(i).name == name
    )
    section._advantage_combo.setCurrentIndex(index)


def test_name_sort_reorders_the_model(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),
            AdvantageSelection("Agile Grab", 1),
            AdvantageSelection("Animal Empathy", 1),
        ]
    )
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(SORT_NAME))

    # A preset permanently rewrites the saved order (so it persists on save).
    assert _names(section) == ["Agile Grab", "Animal Empathy", "Assessment"]


def test_rank_sort_is_high_to_low(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 2),
            AdvantageSelection("Benefit", 5),
            AdvantageSelection("Close Attack", 3),
        ]
    )
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(SORT_RANK))

    assert [s.rank for s in section._character.advantages] == [5, 3, 2]


def test_type_sort_groups_by_type(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),  # General
            AdvantageSelection("Agile Grab", 1),  # Combat
            AdvantageSelection("Animal Empathy", 1),  # Skill
        ]
    )
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(SORT_TYPE))

    # Combat < General < Skill alphabetically.
    assert _names(section) == ["Agile Grab", "Assessment", "Animal Empathy"]


def test_dragging_a_row_mutates_the_model(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),
            AdvantageSelection("Agile Grab", 1),
            AdvantageSelection("Animal Empathy", 1),
        ]
    )
    assert section._sort_mode == SORT_MANUAL

    advantages = section._character.advantages
    # Animal Empathy, dropped on the near side of Agile Grab.
    section.move_advantage(advantages[2], advantages[1], before=True)

    assert _names(section) == ["Assessment", "Animal Empathy", "Agile Grab"]


def test_dropping_a_row_on_itself_is_a_no_op(qapp: QApplication) -> None:
    section = _section([AdvantageSelection("Assessment", 1), AdvantageSelection("Benefit", 1)])
    first = section._character.advantages[0]
    section.move_advantage(first, first, before=True)

    assert _names(section) == ["Assessment", "Benefit"]


def test_a_row_dropped_past_the_last_one_goes_to_the_end(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),
            AdvantageSelection("Agile Grab", 1),
            AdvantageSelection("Animal Empathy", 1),
        ]
    )
    advantages = section._character.advantages
    section.move_advantage(advantages[0], advantages[2], before=False)

    assert _names(section) == ["Agile Grab", "Animal Empathy", "Assessment"]


def test_the_row_menu_removes_that_advantage_alone(qapp: QApplication) -> None:
    """Removal is by identity: two Benefits are two rows, and one goes."""
    section = _section(
        [
            AdvantageSelection("Benefit", 1, "Wealth"),
            AdvantageSelection("Assessment", 1),
            AdvantageSelection("Benefit", 1, "Status"),
        ]
    )
    table, row, selection = section._row_refs[2]
    assert section._remove_label(table, row) == "Remove Benefit"

    section._remove_row(table, row)

    assert [s.parameter for s in section._character.advantages if s.name == "Benefit"] == ["Wealth"]
    assert selection not in section._character.advantages


def test_a_locked_block_offers_no_remove(qapp: QApplication) -> None:
    section = _section([AdvantageSelection("Assessment", 1)])
    section.set_locked(True)

    table, row, _ = section._row_refs[0]
    assert section._remove_label(table, row) is None
    # ...and the drag stands down with it: reordering is a build edit.
    assert section._reorder._enabled() is False


def test_row_refs_map_every_advantage(qapp: QApplication) -> None:
    selections = [AdvantageSelection("Assessment", i + 1) for i in range(3)]
    section = _section(selections)

    # One row reference per advantage, each pointing at a real model object.
    assert len(section._row_refs) == 3
    referenced = {id(sel) for _, _, sel in section._row_refs}
    assert referenced == {id(s) for s in section._character.advantages}


# -- parameter (subject) input -------------------------------------------------


def test_add_with_choice_parameter_stores_the_chosen_value(qapp: QApplication) -> None:
    section = _section([])
    _pick(section, "Skill Mastery")  # optionsFrom: skills

    assert section._advantage_param.count() > 0  # populated from the skill list
    value = section._advantage_param.itemData(1)  # some skill's name
    section._advantage_param.setCurrentIndex(1)
    section._add_advantage()

    added = section._character.advantages[-1]
    assert added.name == "Skill Mastery"
    assert added.parameter == value
    assert section._parameter_display(added) == value


def test_add_with_text_parameter_stores_the_typed_value(qapp: QApplication) -> None:
    section = _section([])
    _pick(section, "Benefit")  # kind: text

    section._advantage_param_text.setText("Wealth")
    section._add_advantage()

    added = section._character.advantages[-1]
    assert added.parameter == "Wealth"
    # The subject is folded into the rendered row text.
    table, row, _ = section._row_refs[-1]
    assert "(Wealth)" in table.item(row, 0).text()


def test_alternate_initiative_display_uses_the_ability_name(qapp: QApplication) -> None:
    section = _section([AdvantageSelection("Alternate Initiative", 1, "INT")])
    added = section._character.advantages[0]
    assert section._parameter_display(added) == "Intellect"


def test_alternate_initiative_offers_only_the_mental_abilities(qapp: QApplication) -> None:
    # A dynamic ``abilities`` source restricted by ``options`` must list only that
    # subset (INT/AWE/PRE), not every ability on the sheet.
    section = _section([])
    advantage = next(a for a in section._data.advantages if a.name == "Alternate Initiative")
    options = section._parameter_options(advantage.parameter)
    assert [value for value, _label in options] == ["INT", "AWE", "PRE"]


def test_parameter_survives_save_and_load(qapp: QApplication) -> None:
    section = _section([])
    _pick(section, "Benefit")
    section._advantage_param_text.setText("Security clearance")
    section._add_advantage()

    restored = Character.from_dict(section._character.to_dict())
    assert restored.advantages[-1].parameter == "Security clearance"


def test_refresh_power_options_reflects_the_characters_powers(qapp: QApplication) -> None:
    section = _section([])
    _pick(section, "Improved Critical")  # optionsFrom: powers
    assert section._advantage_param.count() == 0  # no powers yet

    section._character.powers.append(Power(name="Fire Blast"))
    section.refresh_power_options()
    assert section._advantage_param.findData("Fire Blast") >= 0


def test_edit_advantage_updates_rank_and_subject(qapp, monkeypatch) -> None:
    section = _section([AdvantageSelection("Benefit", 1, "Wealth")])
    selection = section._character.advantages[0]

    def fake_exec(dialog: QDialog) -> int:
        dialog.findChild(QLineEdit).setText("Fame")
        dialog.findChild(QSpinBox).setValue(3)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    section._edit_advantage(selection)

    assert selection.parameter == "Fame"
    assert selection.rank == 3


def test_heroic_ranks_free_backs_both_the_picker_cap_and_the_add_refusal() -> None:
    """One helper, so the rank ceiling the picker offers and the limit the add
    enforces cannot drift apart."""
    from mm_companion.core.rules import (
        HEROIC_TYPE,
        heroic_advantage_budget,
        heroic_advantage_ranks,
        heroic_advantage_ranks_free,
    )

    data = load_game_data()
    char = Character.new_default(data)
    char.power_level = 10  # budget = 5

    assert heroic_advantage_ranks_free(char, data) == heroic_advantage_budget(10)

    heroic = next(a for a in data.advantages if HEROIC_TYPE in a.types and a.ranked)
    char.advantages.append(AdvantageSelection(heroic.name, rank=3))
    free = heroic_advantage_ranks_free(char, data)
    assert free == heroic_advantage_budget(10) - heroic_advantage_ranks(char, data)
    assert free == 2

    # Over-budget reports negative rather than clamping — the callers decide.
    char.advantages.append(AdvantageSelection(heroic.name, rank=9))
    assert heroic_advantage_ranks_free(char, data) < 0


# -- the Advantage column's width ----------------------------------------------

#: An advantage whose subject the player types, which is what makes the column's
#: content unbounded and is the whole reason it is capped.
LONG_SUBJECT = "Wealthy — a controlling stake in a multiplanetary mega-corporation"


def _shown(section: AdvantagesSection, width: int = 900) -> AdvantagesSection:
    section.resize(width, 700)
    section.show()
    for _ in range(10):
        QApplication.processEvents()
    return section


def test_a_long_advantage_name_wraps_instead_of_widening_the_block(
    qapp: QApplication,
) -> None:
    """The Advantage column stops at its cap and the name takes a second line.

    It used to be ``ResizeToContents``, so a subject the player typed grew the column
    without limit: the stretching Description column paid for it, and the block
    demanded that much room wherever it was put.
    """
    section = _shown(
        _section(
            [
                AdvantageSelection(name="Benefit", rank=3, parameter=LONG_SUBJECT),
                AdvantageSelection(name="Improved Initiative", rank=2),
            ]
        )
    )
    table = section._tables[0]
    row = next(entry.row for entry in section._row_refs if entry.key.name == "Benefit")
    text = table.item(row, 0).text()

    assert section._name_col_width() == name_max_width()
    assert table.columnWidth(0) == name_max_width()
    # Too long for that share, so the row grew rather than the column.
    assert table.fontMetrics().horizontalAdvance(text) > table.columnWidth(0)
    assert table.rowHeight(row) >= table.sizeHintForRow(row)


def test_a_typed_subject_does_not_widen_what_a_panel_demands(qapp: QApplication) -> None:
    """``_min_col_width`` is the flow's divisor and the section's reported minimum.

    So while it tracked the name column's raw content, one long subject was enough to
    drop the block to a single panel and pin the page open at it. The claim is that it
    has *stopped growing* — asserted by making the subject three times longer again,
    which is font-independent in a way that comparing against a short one is not.
    """
    long = _section([AdvantageSelection(name="Benefit", rank=1, parameter=LONG_SUBJECT)])
    longer = _section(
        [AdvantageSelection(name="Benefit", rank=1, parameter=" ".join([LONG_SUBJECT] * 3))]
    )

    assert long._min_col_width() == longer._min_col_width()
    assert long._name_col_width() == name_max_width()


def test_the_advantage_header_is_never_clipped(qapp: QApplication) -> None:
    """The floor: an empty block still has "Advantage" to print."""
    section = _shown(_section([]))

    header = section._tables[0].fontMetrics().horizontalAdvance("Advantage")
    assert section._name_col_width() >= header


# -- power-granted advantages -------------------------------------------------


def _granted_section(rows) -> AdvantagesSection:
    """A section whose character has one Enhanced Trait allocating ``(trait, ranks)``."""
    from mm_companion.core.powers import PowerEffectInstance

    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(
        Power(
            name="Berserker Rage",
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=sum(r for _t, r in rows),
                    config={"traits": [{"trait": t, "ranks": r} for t, r in rows]},
                )
            ],
        )
    )
    return AdvantagesSection(data, char)


def _row_texts(section: AdvantagesSection) -> list[tuple[str, str]]:
    """Every rendered ``(name, description)`` pair across the section's panels."""
    return [
        (table.item(row, 0).text(), table.item(row, 2).text())
        for table in section._tables
        for row in range(table.rowCount())
    ]


def test_a_power_granted_advantage_is_shown_and_names_its_power(qapp: QApplication) -> None:
    section = _granted_section([("Fearless", 2)])
    names = [name for name, _desc in _row_texts(section)]
    assert "Fearless 2" in names
    description = next(desc for name, desc in _row_texts(section) if name == "Fearless 2")
    assert description.startswith("From Berserker Rage.")


def test_a_granted_advantage_is_not_the_players_to_edit(qapp: QApplication) -> None:
    section = _granted_section([("Fearless", 2)])
    # Not in the model list, so nothing writes it back to the character...
    assert section._character.advantages == []
    # ...and not in the index the reorder/remove/edit gestures address.
    assert list(section._row_refs) == []
    assert [selection.name for _t, _r, selection in section._granted_refs] == ["Fearless"]


def test_a_granted_advantage_costs_no_advantage_points(qapp: QApplication) -> None:
    from mm_companion.core.rules import advantage_points_spent, heroic_advantage_ranks

    section = _granted_section([("Fearless", 2)])
    data = load_game_data()
    assert advantage_points_spent(section._character, data) == 0
    assert heroic_advantage_ranks(section._character, data) == 0


def test_switching_the_granting_power_off_drops_the_row(qapp: QApplication) -> None:
    section = _granted_section([("Fearless", 2)])
    assert "Fearless 2" in [name for name, _desc in _row_texts(section)]

    section._character.powers[0].activated = False
    section.refresh_granted()
    assert "Fearless 2" not in [name for name, _desc in _row_texts(section)]


def test_a_granted_advantage_shows_the_subject_it_was_granted_for(qapp: QApplication) -> None:
    """Improved Critical is bought per attack, and granted per attack too — a row that
    dropped the subject would be a row nothing on the sheet could act on."""

    section = _granted_section([("Improved Critical::Sword", 1)])
    names = [name for name, _desc in _row_texts(section)]
    assert "Improved Critical 1 (Sword)" in names


def test_the_same_advantage_granted_twice_reads_as_two_rows(qapp: QApplication) -> None:
    section = _granted_section([("Improved Critical::Sword", 1), ("Improved Critical::Bow", 1)])
    names = [name for name, _desc in _row_texts(section)]
    assert "Improved Critical 1 (Sword)" in names
    assert "Improved Critical 1 (Bow)" in names

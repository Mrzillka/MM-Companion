"""The skills block's ordering, removal, reset and row-menu behaviour."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.sections.row_table import SORT_MANUAL, build_row_menu
from mm_companion.ui.sections.skills import COL_ABILITY as S_COL_ABILITY
from mm_companion.ui.sections.skills import COL_NAME as S_COL_NAME
from mm_companion.ui.sections.skills import HEADERS as S_HEADERS
from mm_companion.ui.sections.skills import (
    SORT_ABILITY,
    SORT_ALPHA,
    SORT_RANK,
    SORT_TOTAL,
    SkillRowKey,
    SkillsSection,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _section(character: Character | None = None) -> SkillsSection:
    data = load_game_data()
    return SkillsSection(data, character or Character.new_default(data))


def _visible(section: SkillsSection) -> list[str]:
    return [skill.name for skill in section._visible_skills()]


def _row_for(section: SkillsSection, key: SkillRowKey):
    """The (table, row) a given row key is rendered at."""
    entry = section._row_refs.find(key)
    assert entry is not None, f"no row for {key}"
    return entry.table, entry.row


# -- what is shown, and in what order ------------------------------------------


def test_a_fresh_character_shows_the_rulesets_order(qapp: QApplication) -> None:
    section = _section()

    assert _visible(section) == [skill.name for skill in section._data.skills]
    # Nothing is stored until the player actually changes something.
    assert section._character.skill_order == []
    assert section._character.hidden_skills == []


def test_a_stored_order_wins_and_unlisted_skills_trail_it(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_order = ["Stealth", "Acrobatics"]
    section = SkillsSection(data, char)

    names = _visible(section)
    assert names[:2] == ["Stealth", "Acrobatics"]
    # Everything the stored order does not mention keeps the ruleset's own order.
    rest = [s.name for s in data.skills if s.name not in ("Stealth", "Acrobatics")]
    assert names[2:] == rest


def test_a_stored_name_the_ruleset_lost_is_kept_not_pruned(qapp: QApplication) -> None:
    """A skill from a mod that is off today comes back where the player left it."""
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_order = ["Acrobatics", "Thaumaturgy", "Athletics"]
    section = SkillsSection(data, char)

    assert "Thaumaturgy" in section._ordered_skill_names()
    assert "Thaumaturgy" not in _visible(section)


# -- dragging a row ------------------------------------------------------------


def test_moving_a_skill_writes_the_order(qapp: QApplication) -> None:
    section = _section()
    section.move_skill("Stealth", "Acrobatics", before=True)

    assert _visible(section)[0] == "Stealth"
    assert section._character.skill_order[:2] == ["Stealth", "Acrobatics"]


def test_a_skill_dropped_after_a_row_lands_after_it(qapp: QApplication) -> None:
    section = _section()
    section.move_skill("Acrobatics", "Athletics", before=False)

    assert _visible(section)[:2] == ["Athletics", "Acrobatics"]


def test_a_skill_carries_its_focus_rows_with_it(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science", "Streetwise"]
    section = SkillsSection(data, char)
    section.move_skill("Expertise", "Acrobatics", before=True)

    # The focus rows are rendered *from* the skill, so they simply follow it.
    assert _visible(section)[0] == "Expertise"
    assert section._row_refs[0].key == SkillRowKey("skill", "Expertise")
    assert section._row_refs[1].key.name == "Science"


def test_focuses_reorder_within_their_own_skill(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science", "Streetwise", "Law"]
    section = SkillsSection(data, char)

    section.move_sub_row(SkillRowKey("focus", "Expertise", "Law"), "Science", before=True)

    assert char.focuses["Expertise"] == ["Law", "Science", "Streetwise"]


def test_a_focus_may_not_be_dropped_into_another_skill(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science"]
    char.focuses["Close Combat"] = ["Unarmed"]
    section = SkillsSection(data, char)

    science = section._row_refs.find(
        SkillRowKey("focus", "Expertise", "Science", "Expertise::Science")
    )
    unarmed = section._row_refs.find(
        SkillRowKey("focus", "Close Combat", "Unarmed", "Close Combat::Unarmed")
    )
    assert section._accepts_drop(science, unarmed, True) is False
    # ...while a skill row may land beside any skill but its own.
    acrobatics = section._row_refs.find(SkillRowKey("skill", "Acrobatics", "", "Acrobatics"))
    assert section._accepts_drop(acrobatics, unarmed, True) is True
    assert section._accepts_drop(acrobatics, acrobatics, True) is False


# -- removing a row ------------------------------------------------------------


def test_removing_a_skill_drops_everything_bought_on_it(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_ranks["Stealth"] = 6
    char.specializations["Stealth"] = ["Shadows"]
    char.skill_ranks["Stealth::spec::Shadows"] = 4
    char.skill_ranks["Acrobatics"] = 2
    section = SkillsSection(data, char)

    section.remove_skill("Stealth", confirm=False)

    assert "Stealth" not in _visible(section)
    assert char.hidden_skills == ["Stealth"]
    assert "Stealth" not in char.skill_ranks
    assert "Stealth::spec::Shadows" not in char.skill_ranks
    assert "Stealth" not in char.specializations
    # Its neighbours are untouched.
    assert char.skill_ranks["Acrobatics"] == 2


def test_removing_an_empty_skill_needs_no_confirmation(qapp: QApplication) -> None:
    """Only a skill with something to lose asks; the rest go on one click."""
    section = _section()

    assert section._skill_has_anything_bought("Stealth") is False
    section._character.skill_ranks["Stealth"] = 3
    assert section._skill_has_anything_bought("Stealth") is True


def test_the_row_menu_offers_pin_and_remove_worded_for_the_row(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science"]
    char.specializations["Stealth"] = ["Shadows"]
    section = SkillsSection(data, char)

    for key, label in (
        (SkillRowKey("skill", "Acrobatics", "", "Acrobatics"), "Remove Acrobatics"),
        (
            SkillRowKey("focus", "Expertise", "Science", "Expertise::Science"),
            "Remove Expertise: Science",
        ),
        (
            SkillRowKey("spec", "Stealth", "Shadows", "Stealth::spec::Shadows"),
            "Remove Stealth: Shadows (specialized)",
        ),
    ):
        table, row = _row_for(section, key)
        assert section._remove_label(table, row) == label


def test_removing_a_focus_from_the_menu_drops_its_ranks(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science"]
    char.skill_ranks["Expertise::Science"] = 5
    section = SkillsSection(data, char)

    table, row = _row_for(
        section, SkillRowKey("focus", "Expertise", "Science", "Expertise::Science")
    )
    section._remove_row(table, row)

    assert char.focuses["Expertise"] == []
    assert "Expertise::Science" not in char.skill_ranks


def test_a_locked_block_offers_no_remove_and_no_drag(qapp: QApplication) -> None:
    section = _section()
    table, row = _row_for(section, SkillRowKey("skill", "Acrobatics", "", "Acrobatics"))
    # The pin entry is absent too (no GM card behind this sheet), so the menu is
    # empty — which is what makes a right-click show nothing at all.
    contributors = (lambda menu, t, r: None,)
    assert build_row_menu(table, row, contributors).actions() == []

    section.set_locked(True)
    table, row = _row_for(section, SkillRowKey("skill", "Acrobatics", "", "Acrobatics"))
    assert section._remove_label(table, row) is None
    assert section._reorder._enabled() is False


# -- reset ---------------------------------------------------------------------


def test_reset_restores_the_order_and_the_removed_rows(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    section = SkillsSection(data, char)
    section.move_skill("Stealth", "Acrobatics", before=True)
    section.remove_skill("Vehicles", confirm=False)

    section.reset_skills()

    assert char.skill_order == []
    assert char.hidden_skills == []
    assert _visible(section) == [skill.name for skill in data.skills]
    assert section._sort.mode() == SORT_MANUAL


def test_reset_does_not_bring_back_what_a_removal_dropped(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_ranks["Stealth"] = 6
    section = SkillsSection(data, char)
    section.remove_skill("Stealth", confirm=False)

    section.reset_skills()

    assert "Stealth" in _visible(section)
    assert "Stealth" not in char.skill_ranks


def test_a_restored_focused_skill_still_renders(qapp: QApplication) -> None:
    """Removing a focused skill drops its focus list; reset must reseed it."""
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science"]
    section = SkillsSection(data, char)
    section.remove_skill("Expertise", confirm=False)

    section.reset_skills()

    assert "Expertise" in _visible(section)
    assert char.focuses["Expertise"] == []


# -- sorting -------------------------------------------------------------------


def _sort_to(section: SkillsSection, mode: str) -> list[str]:
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(mode))
    return section._character.skill_order


def test_alphabetical_sort_rewrites_the_stored_order(qapp: QApplication) -> None:
    section = _section()
    order = _sort_to(section, SORT_ALPHA)

    assert order == sorted(order, key=str.lower)
    # A preset sort is permanent: the new order is the one that saves.
    assert _visible(section) == [name for name in order]


def test_ability_sort_groups_by_the_linked_ability(qapp: QApplication) -> None:
    section = _section()
    order = _sort_to(section, SORT_ABILITY)

    abilities = [section._ability_of(name) for name in order]
    assert abilities == sorted(abilities)


def test_rank_sort_is_high_to_low_and_reads_focus_rows(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_ranks["Stealth"] = 4
    # A focused skill has no pool of its own; its best focus stands for it.
    char.focuses["Expertise"] = ["Science"]
    char.skill_ranks["Expertise::Science"] = 9
    section = SkillsSection(data, char)

    order = _sort_to(section, SORT_RANK)

    assert order[0] == "Expertise"
    assert order[1] == "Stealth"


def test_total_sort_reads_the_derived_total(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["AGL"] = 5  # lifts every AGL skill's total
    char.skill_ranks["Stealth"] = 2
    section = SkillsSection(data, char)

    order = _sort_to(section, SORT_TOTAL)

    assert order[0] == "Stealth"


def test_a_preset_sort_stands_the_drag_down(qapp: QApplication) -> None:
    section = _section()
    assert section._reorder._enabled() is True

    _sort_to(section, SORT_ALPHA)

    assert section._reorder._enabled() is False


# -- column widths -------------------------------------------------------------


def _shown(section: SkillsSection, width: int, height: int = 900) -> SkillsSection:
    section.resize(width, height)
    section.show()
    for _ in range(10):
        QApplication.processEvents()
    return section


def test_the_group_header_spans_every_column(qapp: QApplication) -> None:
    """Including the name's, which is the whole point.

    A ``ResizeToContents`` column measures a spanned cell widget as its own
    content, so the "Add focus…" buttons parked on the Ability column made that
    column as wide as they are. Spanning from the name column instead puts them on
    the one column that stretches, where a wide widget cannot distort anything.
    """
    data = load_game_data()
    section = _shown(SkillsSection(data, Character.new_default(data)), 1000)

    table, row = _row_for(section, SkillRowKey("skill", "Expertise"))
    assert table.columnSpan(row, S_COL_NAME) == len(S_HEADERS)
    # The Ability column is back to the width of a three-letter code.
    assert table.columnWidth(S_COL_ABILITY) < 2 * section._ability_col_width()


@pytest.mark.parametrize("width", [700, 1000, 1100, 1300, 1500])
def test_no_skill_name_is_clipped_at_any_width(qapp: QApplication, width: int) -> None:
    """The panel count is chosen so every name fits; the flow must budget for it.

    The Ability column was missing from ``_min_col_width``, so at some widths the
    flow fitted one panel too many and the stretching name column silently paid the
    difference — which showed up as a long focus name being cut off. Swept across
    widths because *which* ones broke was an accident of where the panel-count
    boundaries happened to fall, and those move with the font: a single width would
    have caught this on one machine and missed it on the next.
    """
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Science", "Streetwise"]
    char.specializations["Stealth"] = ["Urban"]
    section = _shown(SkillsSection(data, char), width)

    for entry in section._row_refs:
        item = entry.table.item(entry.row, S_COL_NAME)
        if item is None:
            continue  # a header or a row whose name cell carries the ＋ control
        needed = entry.table.fontMetrics().horizontalAdvance(item.text())
        assert entry.table.columnWidth(S_COL_NAME) >= needed, item.text()

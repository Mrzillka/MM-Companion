"""The skills block's ordering, removal, reset and row-menu behaviour."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.ui.sections.row_table import SORT_MANUAL, build_row_menu
from mm_companion.ui.sections.skills import COL_ABILITY as S_COL_ABILITY
from mm_companion.ui.sections.skills import COL_NAME as S_COL_NAME
from mm_companion.ui.sections.skills import COL_RANKS as S_COL_RANKS
from mm_companion.ui.sections.skills import COL_TOTAL as S_COL_TOTAL
from mm_companion.ui.sections.skills import HEADERS as S_HEADERS
from mm_companion.ui.sections.skills import (
    SORT_ABILITY,
    SORT_ALPHA,
    SORT_RANK,
    SORT_TOTAL,
    SkillRowKey,
    SkillsSection,
    name_max_width,
)
from mm_companion.ui.sections.stat_table import ROLL_ROLE


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
    """Every name is shown in full — on a second line if that is what it takes.

    Two things have to hold, and they are the halves of one bargain.

    The name column is never squeezed below its capped share: the Ability column was
    once missing from ``_min_col_width``, so at some widths the flow fitted one panel
    too many and the stretching name column silently paid the difference.

    And every row is at least as tall as the view says its content needs. That is
    the honest reading of "nothing is elided": ``wordWrap`` is on, so
    ``sizeHintForRow`` is Qt's own answer for the room the row's lines take, and a
    row that tall leaves the delegate no reason to cut anything. It is also the
    assertion that catches the real bug — the rows were being fitted *before* the
    ResizeToContents columns took their share, so they were sized for a name column
    wider than they ended up with, and the last line went missing.

    Swept across widths because *which* ones broke was an accident of where the
    panel-count boundaries happened to fall, and those move with the font: a single
    width would have caught this on one machine and missed it on the next.
    """
    data = load_game_data()
    char = Character.new_default(data)
    # One focus long enough that its row genuinely has to wrap at every width in the
    # sweep — with only short ones the height assertion below passes vacuously, the
    # default row height already covering two lines of them.
    char.focuses["Expertise"] = ["Interstellar Xenobiology and Comparative Anatomy", "Law"]
    char.specializations["Stealth"] = ["Urban Infiltration and Countersurveillance"]
    section = _shown(SkillsSection(data, char), width)

    for entry in section._row_refs:
        item = entry.table.item(entry.row, S_COL_NAME)
        if item is None:
            continue  # a header or a row whose name cell carries the ＋ control
        table, row = entry.table, entry.row
        needed = table.fontMetrics().horizontalAdvance(item.text())
        assert table.columnWidth(S_COL_NAME) >= min(needed, name_max_width()), item.text()
        assert table.rowHeight(row) >= table.sizeHintForRow(row), item.text()


#: Long enough to pass the name column's cap in any font, and two words wide so it
#: has somewhere to break (see ``wrapping_column_width`` on why one long word is a
#: different case).
LONG_FOCUS = "Interstellar Xenobiology and Comparative Anatomy"


def _with_long_focus(width: int) -> SkillsSection:
    """A sheet whose Expertise carries one very long focus and one short one."""
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = [LONG_FOCUS, "Law"]
    return _shown(SkillsSection(data, char), width)


def test_a_long_focus_name_does_not_cost_the_block_its_second_panel(
    qapp: QApplication,
) -> None:
    """``_min_col_width`` is the flow's divisor *and* the section's reported minimum.

    So while it tracked the widest label without a ceiling, a single typed focus name
    pushed a panel wider than the block would ever be given: one column, and the name
    cut off inside it. Capped, the panel stays a panel.
    """
    assert len(_with_long_focus(1100)._tables) > 1


def test_a_focus_name_too_wide_for_its_column_wraps(qapp: QApplication) -> None:
    """Measured where the label is *certain* not to fit: one narrow panel.

    At a roomy width the column may well be wide enough for it, and then there is
    nothing to prove — which is how this passed with the wrapping switched off. The
    two focuses are the comparison: same table, same kind of row, so the only thing
    the taller one has is more lines.

    Narrow enough that the shed order cannot rescue it either: a panel that has given
    up its numeric columns hands the whole width to the stretching name, and 420 was
    once tight and is now roomy.
    """
    section = _with_long_focus(300)
    table, row = _row_for(
        section, SkillRowKey("focus", "Expertise", LONG_FOCUS, f"Expertise::{LONG_FOCUS}")
    )
    _, short_row = _row_for(section, SkillRowKey("focus", "Expertise", "Law", "Expertise::Law"))

    text = table.item(row, S_COL_NAME).text()
    assert table.fontMetrics().horizontalAdvance(text) > table.columnWidth(S_COL_NAME)
    assert table.rowHeight(row) > table.rowHeight(short_row)


def test_the_name_column_is_capped_by_its_theme_metric(qapp: QApplication) -> None:
    """A label longer than the cap adds nothing to what a panel demands.

    Which is the whole point of the cap: past it the text wraps, so the block's
    minimum stops tracking how much a player typed.
    """
    data = load_game_data()
    short = Character.new_default(data)
    short.focuses["Expertise"] = ["Law"]
    long = Character.new_default(data)
    long.focuses["Expertise"] = ["Interstellar Xenobiology and Comparative Anatomy"]

    narrow = SkillsSection(data, short)._min_col_width()
    wide = SkillsSection(data, long)._min_col_width()

    assert wide >= narrow
    assert wide - narrow <= name_max_width()


def test_the_block_asks_for_a_panel_floor_rather_than_a_comfortable_one(
    qapp: QApplication,
) -> None:
    """Two different width questions, and one answer to both is what made a squeezed
    block *clip*.

    ``_min_col_width`` is what a panel reads well at, and it is the right divisor for
    "how many of these fit". Reported as the section's minimum it is a refusal — the
    frame hands the section the larger of the viewport and that number, so the table
    never got narrow enough to shed a column and what would not fit was simply cut
    off. ``_panel_floor_width`` is the narrowest a panel knows how to *reach*, and it
    may move with neither the data nor the **lock** (see ``tests/test_lock_geometry.py``
    for the rule that a lock toggle may change a block's height but never its width).
    """
    data = load_game_data()
    char = Character.new_default(data)
    char.focuses["Expertise"] = ["Interstellar Xenobiology and Comparative Anatomy"]
    section = _shown(SkillsSection(data, char), 1100)
    floor = section.minimumSizeHint().width()

    assert floor == section._panel_floor_width()
    assert floor < section._min_col_width(), "the floor is a refusal, not a preference"

    section.set_locked(True)
    QApplication.processEvents()

    assert section.minimumSizeHint().width() == floor


# -- the "+" column appears only when something modifies a row --------------------


def test_a_medium_character_shows_no_modifier_column(qapp: QApplication) -> None:
    """Size contributes *nothing* at Medium, not zero.

    A ``TraitBonus`` is always truthy, so a zero-amount contribution would switch this
    column on for every character in the game.
    """
    assert _section()._show_mods is False


def test_a_large_character_shows_it_for_the_skills_size_touches(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = "Large"
    section = SkillsSection(data, char)

    assert section._show_mods is True


# --- rows a power granted -------------------------------------------------------------


def _with_enhanced(row_id: str, ranks: int, name: str = "Chameleon Field") -> Character:
    """A character whose one power raises ``row_id`` by ``ranks``."""

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(
            name=name,
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=ranks,
                    config={"traits": [{"trait": row_id, "ranks": ranks}]},
                )
            ],
        )
    )
    return character


def _granted_row(section: SkillsSection, display: str):
    """The (table, row) a granted row is drawn at, found by its rendered name."""

    for table in section._tables:
        for row in range(table.rowCount()):
            item = table.item(row, S_COL_NAME)
            if item is not None and item.text().strip() == display:
                return table, row
    return None


def test_a_granted_focus_gets_a_row_the_character_never_bought(qapp: QApplication) -> None:
    """An Enhanced Trait naming a focus the hero has no row for still has to show:
    the power paid for it, and a bonus with nowhere to land reads as a power that does
    nothing."""

    section = _section(_with_enhanced("Expertise::Stealth", 2))
    found = _granted_row(section, "Expertise: Stealth")
    assert found is not None
    table, row = found
    # Nothing about it is the player's to edit, so there is no rank spin — a read-only
    # dash in its place — and no row key for the reorder/remove gestures to address.
    assert table.cellWidget(row, S_COL_RANKS) is None
    key = SkillRowKey("focus", "Expertise", "Stealth", "Expertise::Stealth")
    assert section._row_refs.find(key) is None
    assert "granted by Chameleon Field" in table.item(row, S_COL_NAME).toolTip()


def test_a_granted_row_is_still_rollable(qapp: QApplication) -> None:
    """A granted focus is a real skill; the ＋2 is on it and it rolls like any other."""

    section = _section(_with_enhanced("Expertise::Stealth", 2))
    table, row = _granted_row(section, "Expertise: Stealth")
    assert table.item(row, S_COL_TOTAL).data(ROLL_ROLE) == (
        "Expertise::Stealth",
        "Expertise: Stealth",
    )
    assert table.item(row, S_COL_TOTAL).text() == "2"  # INT 0 + 0 ranks + the granted 2


def test_a_focus_the_character_owns_needs_no_granted_row(qapp: QApplication) -> None:
    character = _with_enhanced("Expertise::Stealth", 2)
    character.focuses["Expertise"] = ["Stealth"]
    section = _section(character)
    # One row, and it is the bought one — addressable, with its own rank spin.
    key = SkillRowKey("focus", "Expertise", "Stealth", "Expertise::Stealth")
    assert section._row_refs.find(key) is not None
    table, row = _granted_row(section, "Expertise: Stealth")
    assert table.cellWidget(row, S_COL_RANKS) is not None


def test_losing_the_power_takes_the_granted_row_away(qapp: QApplication) -> None:
    """The row belongs to the power, not to the sheet — it goes when the power does."""

    character = _with_enhanced("Expertise::Stealth", 2)
    section = _section(character)
    assert _granted_row(section, "Expertise: Stealth") is not None
    character.powers.clear()
    section.refresh_granted()
    assert _granted_row(section, "Expertise: Stealth") is None

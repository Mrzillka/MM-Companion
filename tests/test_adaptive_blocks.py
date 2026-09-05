"""What a block does as it is dragged narrower, before it resorts to scrolling.

Three shared decisions, each a pure function so it can be stated directly, and
each carrying the same hysteresis dead-band for the same reason: the reflow
changes the block's height, which can toggle a scrollbar, which changes the width
back across the boundary — an endless relayout otherwise.

The Qt half is checked at the end, on a real sheet, because the arithmetic being
right is not the same as the table actually hiding a column.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.power_constructor.terms_grid import (
    PAIRS_MIN_WIDTH,
    PAIRS_PER_ROW,
    pairs_per_row,
)
from mm_companion.ui.sections.row_table import SHED_HYSTERESIS, columns_to_shed
from mm_companion.ui.sections.stat_table import COL_ABBR, COL_NAME, COL_RANK, COL_TOTAL
from mm_companion.ui.widgets import FORM_WRAP_WIDTH, wraps_form_rows


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


# name, abbreviation, rank, total — the shape of the two stat tables.
WIDTHS = [110, 40, 80, 50]


class TestSheddingColumns:
    def test_a_roomy_table_sheds_nothing(self) -> None:
        assert columns_to_shed(400, WIDTHS, [COL_ABBR]) == ()

    def test_a_tight_table_gives_up_its_worst_column(self) -> None:
        assert columns_to_shed(250, WIDTHS, [COL_ABBR]) == (COL_ABBR,)

    def test_it_stops_once_what_is_left_fits(self) -> None:
        # Two are offered; only one is needed to make the rest fit (240 without the
        # abbreviation, against 245 of room).
        assert columns_to_shed(245, WIDTHS, [COL_ABBR, COL_TOTAL]) == (COL_ABBR,)

    def test_it_keeps_going_while_it_still_does_not_fit(self) -> None:
        assert columns_to_shed(120, WIDTHS, [COL_ABBR, COL_TOTAL]) == (COL_ABBR, COL_TOTAL)

    def test_a_column_nobody_offered_is_never_hidden(self) -> None:
        """The load-bearing ones are the ones left out of the shed order — so a
        table dragged to nothing keeps the columns worth keeping and scrolls."""
        shed = columns_to_shed(10, WIDTHS, [COL_ABBR])

        assert shed == (COL_ABBR,)
        assert COL_NAME not in shed and COL_RANK not in shed and COL_TOTAL not in shed

    def test_a_table_that_offers_nothing_sheds_nothing(self) -> None:
        assert columns_to_shed(10, WIDTHS, []) == ()

    def test_an_unlaid_out_table_sheds_nothing(self) -> None:
        # The first paint is the whole table; the real answer lands on the first
        # resizeEvent, which is the same bargain every other reflow here makes.
        assert columns_to_shed(0, WIDTHS, [COL_ABBR]) == ()
        assert columns_to_shed(-40, WIDTHS, [COL_ABBR]) == ()

    def test_it_does_not_shed_again_until_a_full_band_past_the_boundary(self) -> None:
        # Everything but the abbreviation is 240 wide, so a second column would
        # otherwise start flickering the instant the width dipped under it.
        kept = WIDTHS[COL_NAME] + WIDTHS[COL_RANK] + WIDTHS[COL_TOTAL]
        order = [COL_ABBR, COL_TOTAL]

        assert columns_to_shed(kept - 1, WIDTHS, order, current=(COL_ABBR,)) == (
            COL_ABBR,
            COL_TOTAL,
        )
        held = columns_to_shed(
            kept - 1, WIDTHS, order, current=(COL_ABBR,), hysteresis=SHED_HYSTERESIS
        )
        assert held == (COL_ABBR,)

    def test_the_band_covers_the_first_column_too(self) -> None:
        """It used to stand down whenever nothing was shed yet — which is the one
        state every table starts in, so the transition most worth damping was the
        one that never was."""
        whole = sum(WIDTHS)

        assert columns_to_shed(whole - 1, WIDTHS, [COL_ABBR], current=()) == (COL_ABBR,)
        held = columns_to_shed(
            whole - 1, WIDTHS, [COL_ABBR], current=(), hysteresis=SHED_HYSTERESIS
        )
        assert held == ()

    def test_the_width_it_sheds_at_is_below_the_width_it_restores_at(self) -> None:
        """Why it cannot flicker, stated as one number against another: from any
        arrangement, the width at which another column goes is a band *below* what
        that arrangement needs and the width one comes back at is a band above it,
        so there is no width at which both are true.
        """
        order = [COL_ABBR, COL_TOTAL]
        current = (COL_ABBR,)
        widths = range(1, sum(WIDTHS) + 2 * SHED_HYSTERESIS)

        def answer(available: int) -> tuple[int, ...]:
            return columns_to_shed(
                available, WIDTHS, order, current=current, hysteresis=SHED_HYSTERESIS
            )

        sheds = [w for w in widths if len(answer(w)) > len(current)]
        restores = [w for w in widths if len(answer(w)) < len(current)]

        assert sheds and restores, "the sweep never reached either boundary"
        assert max(sheds) < min(restores)

    def test_it_does_not_take_one_back_until_a_full_band_the_other_way(self) -> None:
        order = [COL_ABBR]
        whole = sum(WIDTHS)

        assert columns_to_shed(whole, WIDTHS, order, current=(COL_ABBR,)) == ()
        held = columns_to_shed(
            whole - 1, WIDTHS, order, current=(COL_ABBR,), hysteresis=SHED_HYSTERESIS
        )
        assert held == (COL_ABBR,)


class TestWrappingAForm:
    def test_a_roomy_form_keeps_its_captions_beside_its_fields(self) -> None:
        assert wraps_form_rows(FORM_WRAP_WIDTH + 1) is False

    def test_a_narrow_form_stacks_them(self) -> None:
        assert wraps_form_rows(FORM_WRAP_WIDTH - 1) is True

    def test_an_unlaid_out_form_does_not_wrap(self) -> None:
        assert wraps_form_rows(0) is False
        assert wraps_form_rows(-10) is False

    def test_it_does_not_flip_back_the_moment_it_crosses_the_line(self) -> None:
        just_over = FORM_WRAP_WIDTH + 1

        assert wraps_form_rows(just_over, currently_wrapped=False) is False
        assert wraps_form_rows(just_over, currently_wrapped=True) is True

    def test_a_caller_may_state_its_own_floor(self) -> None:
        assert wraps_form_rows(300, floor=400) is True
        assert wraps_form_rows(300, floor=200) is False


class TestPairsPerRow:
    def test_a_roomy_card_keeps_two_pairs_abreast(self) -> None:
        assert pairs_per_row(PAIRS_MIN_WIDTH + 10) == PAIRS_PER_ROW

    def test_a_narrow_card_drops_to_one(self) -> None:
        assert pairs_per_row(PAIRS_MIN_WIDTH - 10) == 1

    def test_an_unlaid_out_card_starts_at_two(self) -> None:
        assert pairs_per_row(0) == PAIRS_PER_ROW

    def test_it_holds_its_answer_across_the_boundary(self) -> None:
        edge = PAIRS_MIN_WIDTH

        assert pairs_per_row(edge, current=PAIRS_PER_ROW) == PAIRS_PER_ROW
        assert pairs_per_row(edge, current=1) == 1


# -- and the widgets really do it --------------------------------------------


@pytest.fixture
def squeezed(qapp: QApplication):
    """A roomy sheet the tests below narrow themselves."""
    sheet = CharacterSheet(load_game_data())
    sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    # Wide enough that nothing has reflowed yet, so every test starts from the
    # unsqueezed sheet and squeezes it itself.
    sheet.resize(1700, 800)
    sheet.show()
    _settle(qapp)
    yield sheet
    sheet.hide()
    sheet.deleteLater()


def _settle(qapp: QApplication, rounds: int = 12) -> None:
    for _ in range(rounds):
        qapp.processEvents()


def _narrow_to(sheet, qapp, width: int) -> None:
    """Squeeze the whole sheet, which is the only honest way to squeeze a block.

    Setting a row splitter's sizes directly does not do it: ``QSplitter.setSizes``
    normalises whatever it is given back up to the splitter's own width, so a row
    in a wide sheet stays wide however small the numbers are.
    """
    sheet.resize(width, sheet.height())
    _settle(qapp)


#: A sheet narrow enough that the stat tables have shed their first column and no
#: more. The band is roughly 840-920 and this sits in the middle of it, clear of
#: both edges: shedding is hysteretic, so a width near a boundary answers
#: differently depending on which side the sheet arrived from.
ONE_COLUMN_SHED = 900


def test_a_squeezed_stat_table_drops_the_abbreviation(squeezed, qapp) -> None:
    """It repeats what the name beside it says, which is why it is the *first*
    column the two stat tables are willing to give up."""
    table = squeezed.abilities.table
    assert table.shed_columns() == ()

    _narrow_to(squeezed, qapp, ONE_COLUMN_SHED)

    assert table.shed_columns() == (COL_ABBR,)
    assert table.isColumnHidden(COL_ABBR)
    for column in (COL_NAME, COL_RANK, COL_TOTAL):
        assert not table.isColumnHidden(column), "a load-bearing column was hidden"


def test_a_table_squeezed_further_gives_up_the_rank_too(squeezed, qapp) -> None:
    """Down to the trait and its number, which is what a sheet is read for.

    The build is typed once, at a width the player chooses; the total is read at
    whatever width the page has left. Name and Total are the two that never go.
    """
    table = squeezed.abilities.table

    _narrow_to(squeezed, qapp, 800)

    assert table.shed_columns() == (COL_ABBR, COL_RANK)
    for column in (COL_NAME, COL_TOTAL):
        assert not table.isColumnHidden(column), "a load-bearing column was hidden"


def test_the_total_carries_the_number_once_the_rank_has_gone(squeezed, qapp) -> None:
    """Or a two-column Abilities block would be a column of blank cells: the Total
    is an "and here is what changed" while the rank it changed is beside it, and
    there is nothing beside it any more."""
    section = squeezed.abilities
    strength = section._ability_enh["STR"]
    assert strength.text() == "", "an unmodified trait states its change, not its value"

    _narrow_to(squeezed, qapp, 800)

    assert strength.text() == str(section._abilities["STR"].value())


def test_it_takes_the_column_back_when_the_room_returns(squeezed, qapp) -> None:
    table = squeezed.abilities.table
    _narrow_to(squeezed, qapp, ONE_COLUMN_SHED)
    assert table.shed_columns() == (COL_ABBR,)

    _narrow_to(squeezed, qapp, 1700)

    assert table.shed_columns() == ()
    assert not table.isColumnHidden(COL_ABBR)


def test_a_squeezed_skills_panel_keeps_the_skill_and_its_total(squeezed, qapp) -> None:
    """Same bargain as the stat tables, one column further: the governing ability,
    its rank and the situational modifier go, and then the ranks."""
    from mm_companion.ui.sections.skills import COL_NAME as SKILL_NAME
    from mm_companion.ui.sections.skills import COL_RANKS as SKILL_RANKS
    from mm_companion.ui.sections.skills import COL_TOTAL as SKILL_TOTAL

    table = squeezed.skills._tables[0]

    _narrow_to(squeezed, qapp, 420)

    assert SKILL_RANKS in table.shed_columns(), "the ranks were kept at any width"
    for column in (SKILL_NAME, SKILL_TOTAL):
        assert not table.isColumnHidden(column), "a load-bearing column was hidden"


def test_a_squeezed_advantages_panel_keeps_its_description(squeezed, qapp) -> None:
    """The Type goes; the prose stays and *wraps*.

    A column of prose has a way of getting narrower that a one-word category does
    not, so losing the description outright skipped the very adaptation it was
    best placed to make. Lose the Type, then break the lines, then crop.
    """
    from mm_companion.core.character import AdvantageSelection

    squeezed.character.advantages.extend(
        AdvantageSelection(name=advantage.name) for advantage in squeezed._data.advantages[:6]
    )
    squeezed.reseed()
    squeezed.bus.flush()
    _settle(qapp)
    table = squeezed.advantages._tables[0]

    _narrow_to(squeezed, qapp, 420)

    assert table.shed_columns() == (1,)
    assert not table.isColumnHidden(2), "the prose column was dropped rather than wrapped"


def test_a_flow_block_reports_a_floor_it_can_reach_not_one_it_reads_well_at(squeezed, qapp) -> None:
    """The two are different questions, and answering them the same way is what made
    a narrowed block clip instead of adapt: while the section asked for a comfortable
    panel, the frame handed it that width whatever the viewport did and the table
    never got narrow enough to shed a column."""
    for section in (squeezed.skills, squeezed.advantages):
        assert section.minimumSizeHint().width() < section._min_col_width()


def test_the_system_block_stacks_its_captions_when_squeezed(squeezed, qapp) -> None:
    form = squeezed.system_info._form
    assert form.wrapped is False

    _narrow_to(squeezed, qapp, 780)

    assert form.wrapped is True


def test_a_squeezed_block_never_reports_a_bigger_minimum(squeezed, qapp) -> None:
    """The point of all of it: adapting must not quietly put the floor back."""
    frame = squeezed.block_frame("abilities")
    before = frame.minimumSizeHint().width()

    _narrow_to(squeezed, qapp, 780)

    assert frame.minimumSizeHint().width() == before


def test_a_shed_column_does_not_move_the_block_minimum(squeezed, qapp) -> None:
    """The loop this closes, in one assertion.

    A block hands its section the viewport's width or the section's minimum,
    whichever is larger. While that minimum counted the columns the table was
    willing to *shed*, hiding one narrowed the block, which made the column fit
    again, which widened the block, which hid it again — for as long as the layout
    kept asking, and each answer lays out again inside the one before it, so it
    ended as a stack overflow rather than a flicker.
    """
    table = squeezed.resistances.table
    assert table.shed_columns() == ()
    before = table.minimumSizeHint().width()

    _narrow_to(squeezed, qapp, 560)

    assert COL_ABBR in table.shed_columns(), "the block was never squeezed enough"
    assert table.minimumSizeHint().width() == before


def test_the_whole_table_is_what_a_stat_block_opens_at(squeezed, qapp) -> None:
    """The columns moved from the minimum to the hint rather than being dropped:
    a preference may be content-shaped, a refusal may not."""
    table = squeezed.resistances.table
    widest = sum(table.natural_column_widths())

    assert table.sizeHint().width() >= widest
    assert table.minimumSizeHint().width() < widest


def test_a_hidden_column_still_reports_the_width_it_would_take(squeezed, qapp) -> None:
    """Which is the whole of how a shed column ever comes back.

    Qt cannot answer it: a hidden section's header hint is zero and its items are
    measured without the header's text, so the column reported roughly a third of
    its real width — and the table restored it into a width that could not hold
    it, then shed it again on the resize that followed.
    """
    table = squeezed.resistances.table
    showing = table.natural_column_widths()[COL_ABBR]

    _narrow_to(squeezed, qapp, 560)

    assert COL_ABBR in table.shed_columns(), "the block was never squeezed enough"
    assert table.natural_column_widths()[COL_ABBR] == showing

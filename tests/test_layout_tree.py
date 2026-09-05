"""The arrangement tree: structural operations and the v7 migration (no Qt).

Every drag gesture the canvas can make bottoms out in one of these functions, so
this is where the structural half of resizing, merging, splitting and moving is
actually pinned down. The Qt half only has to draw the answer, which is the whole
reason the model is separated out.
"""

from __future__ import annotations

from mm_companion.ui import layout_tree as lt
from mm_companion.ui.layout_tree import HORIZONTAL, VERTICAL, Leaf, Split

# The historical default page, as rows — the shape every existing layout is in.
DEFAULT_ROWS = [
    ["base_info", "system_info", "character_image"],
    ["abilities", "resistances"],
    ["conditions"],
    ["skills"],
]
KNOWN = {key for row in DEFAULT_ROWS for key in row} | {"dice", "scene", "powers"}


def page() -> Split:
    return lt.rows_to_page(DEFAULT_ROWS)


class TestReadingTheTree:
    def test_keys_come_back_in_reading_order(self) -> None:
        assert lt.keys(page()) == [key for row in DEFAULT_ROWS for key in row]

    def test_a_row_of_one_block_is_a_bare_leaf(self) -> None:
        # Not a horizontal split with a single child: that is what normalize
        # exists to collapse, and a row is not a container in its own right.
        assert page().children[2] == Leaf(("conditions",))

    def test_find_reaches_a_nested_key(self) -> None:
        assert lt.find(page(), "resistances") == (1, 1)
        assert lt.find(page(), "nobody") is None

    def test_keys_of_an_empty_tree(self) -> None:
        assert lt.keys(None) == []
        assert lt.find(None, "abilities") is None


class TestLeaf:
    def test_one_key_is_a_plain_block_and_two_is_a_tab_group(self) -> None:
        assert Leaf(("skills",)).tabbed is False
        assert Leaf(("skills", "powers")).tabbed is True

    def test_active_is_clamped_rather_than_trusted(self) -> None:
        # A settings file can say anything; asking for tab 9 of 2 shows the last.
        assert Leaf(("a", "b"), active=9).active_key() == "b"
        assert Leaf(("a", "b"), active=-3).active_key() == "a"


class TestNormalize:
    def test_a_split_with_one_child_becomes_that_child(self) -> None:
        assert lt.normalize(Split(HORIZONTAL, (Leaf(("skills",)),))) == Leaf(("skills",))

    def test_an_empty_leaf_disappears(self) -> None:
        assert lt.normalize(Leaf(())) is None

    def test_a_split_of_the_same_axis_is_spliced_into_its_parent(self) -> None:
        # h(a, h(b, c)) is just h(a, b, c) — which is what makes a vertical split
        # inside the page mean "more rows" rather than a second container kind.
        nested = Split(HORIZONTAL, (Leaf(("a",)), Split(HORIZONTAL, (Leaf(("b",)), Leaf(("c",))))))
        assert lt.normalize(nested) == Split(HORIZONTAL, (Leaf(("a",)), Leaf(("b",)), Leaf(("c",))))

    def test_a_split_of_the_other_axis_is_left_alone(self) -> None:
        # This is the shape the whole feature exists for: a stack inside a row.
        stack = Split(VERTICAL, (Leaf(("b",)), Leaf(("c",))))
        nested = Split(HORIZONTAL, (Leaf(("a",)), stack))
        assert lt.normalize(nested) == nested

    def test_splicing_keeps_the_sizes_it_can_still_describe(self) -> None:
        """The spliced-in children arrive with no size of their own, and the
        siblings that were already there keep theirs.

        This used to drop the whole run, on the grounds that nothing related the
        grandchildren to their new siblings. A zero means "no size of its own"
        rather than a gap, so the honest answer is available: on the page, where a
        vertical split spliced in *is* more rows, the rows that were already there
        keep the heights somebody dragged them to and the arrivals follow their
        content.
        """
        nested = Split(
            HORIZONTAL,
            (Leaf(("a",)), Split(HORIZONTAL, (Leaf(("b",)), Leaf(("c",))))),
            (100, 200),
        )

        assert lt.normalize(nested).sizes == (100, 0, 0)

    def test_a_run_of_nothing_but_zeros_is_the_same_as_no_sizes(self) -> None:
        split = Split(HORIZONTAL, (Leaf(("a",)), Leaf(("b",))), (0, 0))

        assert lt.normalize(split).sizes == ()

    def test_sizes_survive_when_the_children_do(self) -> None:
        split = Split(HORIZONTAL, (Leaf(("a",)), Leaf(("b",))), (100, 200))
        assert lt.normalize(split).sizes == (100, 200)

    def test_sizes_of_the_wrong_length_are_dropped(self) -> None:
        # Cosmetic numbers degrade rather than rejecting somebody's page.
        split = Split(HORIZONTAL, (Leaf(("a",)), Leaf(("b",))), (100,))
        assert lt.normalize(split).sizes == ()


class TestAsPage:
    def test_a_lone_row_is_still_a_page(self) -> None:
        assert lt.as_page(Leaf(("skills",))) == Split(VERTICAL, (Leaf(("skills",)),))

    def test_a_page_of_nothing_is_an_empty_vertical_split(self) -> None:
        # Every block hidden is a legal thing to render, not an error.
        assert lt.as_page(None) == Split(VERTICAL, ())

    def test_a_row_is_wrapped_rather_than_flattened(self) -> None:
        row = Split(HORIZONTAL, (Leaf(("a",)), Leaf(("b",))))
        assert lt.as_page(row) == Split(VERTICAL, (row,))


class TestRemove:
    def test_a_block_leaves_and_its_row_closes_up(self) -> None:
        after = lt.remove(page(), "conditions")
        assert lt.keys(after) == [k for k in lt.keys(page()) if k != "conditions"]
        assert lt.find(after, "conditions") is None

    def test_the_last_block_of_a_row_takes_the_row_with_it(self) -> None:
        after = lt.remove(page(), "skills")
        assert len(after.children) == len(page().children) - 1

    def test_a_row_left_with_one_block_stops_being_a_split(self) -> None:
        after = lt.remove(page(), "resistances")
        assert after.children[1] == Leaf(("abilities",))

    def test_removing_a_tab_keeps_the_others(self) -> None:
        merged = lt.merge_into(page(), "skills", "abilities")
        after = lt.remove(merged, "skills")
        assert lt.leaf_for(after, "abilities") == Leaf(("abilities",))

    def test_removing_the_active_tab_falls_back_to_the_one_on_its_left(self) -> None:
        leaf = Leaf(("a", "b", "c"), active=2)
        assert lt._remove(leaf, "c") == Leaf(("a", "b"), active=1)

    def test_removing_a_tab_left_of_the_active_one_keeps_it_showing(self) -> None:
        leaf = Leaf(("a", "b", "c"), active=2)
        assert lt._remove(leaf, "a").active_key() == "c"

    def test_a_key_that_is_not_there_changes_nothing(self) -> None:
        before = page()
        assert lt.remove(before, "nobody") is before

    def test_the_survivors_of_a_departure_keep_the_sizes_they_had(self) -> None:
        # A row renormalises them to its real width, so the *proportions* the user
        # dragged survive; the page's are absolute heights that owe nothing to the
        # row that left, and dropping them forgot every height on the sheet the
        # moment one block was closed.
        split = Split(HORIZONTAL, (Leaf(("a",)), Leaf(("b",)), Leaf(("c",))), (10, 20, 30))
        assert lt.remove(split, "b").sizes == (10, 30)

    def test_closing_a_row_leaves_every_other_row_where_it_was(self) -> None:
        page = Split(VERTICAL, (Leaf(("a",)), Leaf(("b",)), Leaf(("c",))), (100, 200, 300))
        assert lt.remove(page, "b").sizes == (100, 300)


class TestInsertBeside:
    def test_beside_joins_the_row_it_is_dropped_into(self) -> None:
        after = lt.insert_beside(page(), "powers", "abilities", "right")
        assert lt.keys(after.children[1]) == ["abilities", "powers", "resistances"]

    def test_left_and_right_put_the_block_on_the_side_named(self) -> None:
        left = lt.insert_beside(page(), "powers", "abilities", "left")
        assert lt.keys(left.children[1]) == ["powers", "abilities", "resistances"]

    def test_below_makes_a_stack_inside_the_row(self) -> None:
        # The headline feature: two blocks one above the other, sharing one row.
        after = lt.insert_beside(page(), "powers", "abilities", "bottom")
        row = after.children[1]
        assert row.orientation == HORIZONTAL
        assert row.children[0] == Split(VERTICAL, (Leaf(("abilities",)), Leaf(("powers",))))
        assert row.children[1] == Leaf(("resistances",))

    def test_above_stacks_the_other_way_up(self) -> None:
        after = lt.insert_beside(page(), "powers", "abilities", "top")
        assert after.children[1].children[0] == Split(
            VERTICAL, (Leaf(("powers",)), Leaf(("abilities",)))
        )

    def test_dropping_below_a_lone_row_makes_a_new_row(self) -> None:
        # A vertical split directly inside the page is not nesting — it is rows.
        after = lt.insert_beside(page(), "powers", "conditions", "bottom")
        assert lt.keys(after) == [
            "base_info", "system_info", "character_image",
            "abilities", "resistances",
            "conditions", "powers",
            "skills",
        ]  # fmt: skip
        assert after.children[3] == Leaf(("powers",))

    def test_an_arrival_takes_half_of_the_block_it_lands_beside(self) -> None:
        # The promise the drop mark makes: the wash fills half the target, so the
        # target is what pays for the arrival and its neighbours do not move. It
        # used to clear the whole run's sizes, which redistributed every cell in it.
        sized = Split(HORIZONTAL, (Leaf(("abilities",)), Leaf(("resistances",))), (300, 400))
        after = lt.insert_beside(sized, "powers", "abilities", "right")
        assert after.sizes == (150, 150, 400)
        assert lt.keys(after) == ["abilities", "powers", "resistances"]

    def test_the_newcomer_never_comes_out_the_larger_of_the_pair(self) -> None:
        sized = Split(HORIZONTAL, (Leaf(("abilities",)), Leaf(("resistances",))), (301, 400))
        assert lt.insert_beside(sized, "powers", "abilities", "right").sizes == (151, 150, 400)
        assert lt.insert_beside(sized, "powers", "abilities", "left").sizes == (150, 151, 400)

    def test_a_run_with_nothing_remembered_still_lays_out_from_hints(self) -> None:
        # Nothing to keep and nothing to halve — the arrival simply joins the run.
        bare = Split(HORIZONTAL, (Leaf(("abilities",)), Leaf(("resistances",))))
        assert lt.insert_beside(bare, "powers", "abilities", "right").sizes == ()

    def test_a_wrapped_cell_divides_the_extent_it_is_given(self) -> None:
        # A drop across the parent's axis wraps the target in a new split, which has
        # no remembered sizes of its own — so the live extent is what it halves.
        page = Split(VERTICAL, (Leaf(("abilities",)), Leaf(("skills",))), (200, 300))
        after = lt.insert_beside(page, "powers", "abilities", "right", extent=480)
        assert after.sizes == (200, 300)  # the rows themselves did not move
        pair = (Leaf(("abilities",)), Leaf(("powers",)))
        assert after.children[0] == Split(HORIZONTAL, pair, (240, 240))

    def test_a_row_nobody_has_dragged_divides_into_two_of_itself(self) -> None:
        # Zero is not a size on the page: it means "be as tall as your content".
        page = Split(VERTICAL, (Leaf(("abilities",)), Leaf(("skills",))), (0, 300))
        after = lt.insert_beside(page, "powers", "abilities", "bottom")
        assert after.sizes == (0, 0, 300)

    def test_a_new_row_does_not_forget_the_heights_of_the_others(self) -> None:
        # A block dragged into a row of its own is no business of the rows already
        # on the page; the newcomer states zero, which means "as tall as your content".
        page = Split(VERTICAL, (Leaf(("a",)), Leaf(("b",))), (100, 200))
        assert lt.append_row(page, "powers", 1).sizes == (100, 0, 200)

    def test_an_unknown_target_lands_the_block_in_a_row_of_its_own(self) -> None:
        after = lt.insert_beside(page(), "powers", "nobody", "right")
        assert after.children[-1] == Leaf(("powers",))

    def test_a_bad_side_is_refused(self) -> None:
        try:
            lt.insert_beside(page(), "powers", "abilities", "sideways")
        except ValueError as exc:
            assert "sideways" in str(exc)
        else:  # pragma: no cover - the assertion above is the test
            raise AssertionError("a nonsense side should not be accepted")


class TestMergeAndSplit:
    def test_a_merge_makes_one_cell_of_two_blocks(self) -> None:
        after = lt.merge_into(page(), "skills", "abilities")
        assert lt.leaf_for(after, "abilities").keys == ("abilities", "skills")

    def test_the_arriving_block_is_the_one_showing(self) -> None:
        after = lt.merge_into(page(), "skills", "abilities")
        assert lt.leaf_for(after, "abilities").active_key() == "skills"

    def test_merging_empties_the_row_the_block_came_from(self) -> None:
        after = lt.merge_into(page(), "skills", "abilities")
        assert len(after.children) == len(page().children) - 1

    def test_a_third_block_joins_the_same_group(self) -> None:
        after = lt.merge_into(lt.merge_into(page(), "skills", "abilities"), "conditions", "skills")
        assert lt.leaf_for(after, "abilities").keys == ("abilities", "skills", "conditions")

    def test_merging_a_block_into_its_own_cell_does_nothing(self) -> None:
        merged = lt.merge_into(page(), "skills", "abilities")
        assert lt.merge_into(merged, "skills", "abilities") is merged

    def test_merging_a_block_with_itself_does_nothing(self) -> None:
        before = page()
        assert lt.merge_into(before, "skills", "skills") is before

    def test_a_split_puts_the_tab_back_beside_its_group(self) -> None:
        merged = lt.merge_into(page(), "skills", "abilities")
        after = lt.split_out(merged, "skills")
        assert lt.leaf_for(after, "abilities") == Leaf(("abilities",))
        assert lt.leaf_for(after, "skills") == Leaf(("skills",))

    def test_splitting_a_block_that_is_already_alone_does_nothing(self) -> None:
        before = page()
        assert lt.split_out(before, "skills") is before

    def test_merge_then_split_returns_the_same_set_of_blocks(self) -> None:
        merged = lt.merge_into(page(), "skills", "abilities")
        assert sorted(lt.keys(lt.split_out(merged, "skills"))) == sorted(lt.keys(page()))


class TestMoveAndActive:
    def test_a_move_takes_the_block_out_of_its_old_row(self) -> None:
        after = lt.move(page(), "skills", "base_info", "right")
        assert lt.keys(after.children[0]) == [
            "base_info",
            "skills",
            "system_info",
            "character_image",
        ]
        assert len(after.children) == len(page().children) - 1

    def test_moving_onto_an_unknown_target_changes_nothing(self) -> None:
        before = page()
        assert lt.move(before, "skills", "nobody", "right") is before

    def test_set_active_brings_a_tab_to_the_front(self) -> None:
        merged = lt.merge_into(page(), "skills", "abilities")
        assert lt.leaf_for(lt.set_active(merged, "abilities"), "skills").active_key() == "abilities"

    def test_set_sizes_records_a_handle_drag(self) -> None:
        after = lt.set_sizes(page(), (1,), [320, 480])
        assert after.children[1].sizes == (320, 480)

    def test_set_sizes_refuses_a_count_that_does_not_fit(self) -> None:
        before = page()
        assert lt.set_sizes(before, (1,), [320]) is before


class TestReorderLeaf:
    """A tab dragged along its bar re-deals one cell, and nothing else."""

    def test_the_keys_come_out_in_the_order_asked_for(self) -> None:
        grouped = lt.merge_into(page(), "skills", "abilities")
        leaf = lt.leaf_for(grouped, "skills")
        after = lt.reorder_leaf(grouped, ("skills",) + tuple(k for k in leaf.keys if k != "skills"))
        assert lt.leaf_for(after, "skills").keys[0] == "skills"

    def test_the_same_block_goes_on_showing(self) -> None:
        grouped = lt.merge_into(page(), "skills", "abilities")
        showing = lt.leaf_for(grouped, "skills").active_key()
        after = lt.reorder_leaf(grouped, ("skills", "abilities"))
        assert lt.leaf_for(after, "skills").active_key() == showing

    def test_a_run_that_is_not_one_whole_cell_is_refused(self) -> None:
        """This re-deals a cell; it is never a way to move a block between two."""
        before = lt.merge_into(page(), "skills", "abilities")
        assert lt.reorder_leaf(before, ("skills", "powers")) is before
        assert lt.reorder_leaf(before, ("skills",)) is before

    def test_an_unknown_block_changes_nothing(self) -> None:
        before = page()
        assert lt.reorder_leaf(before, ("nobody", "skills")) is before
        assert lt.reorder_leaf(before, ()) is before


class TestPersistence:
    def test_a_page_round_trips(self) -> None:
        before = page()
        assert lt.from_dict(lt.to_dict(before), KNOWN) == before

    def test_a_tab_group_round_trips_with_its_active_tab(self) -> None:
        before = lt.merge_into(page(), "skills", "abilities")
        after = lt.from_dict(lt.to_dict(before), KNOWN)
        assert lt.leaf_for(after, "abilities").active_key() == "skills"

    def test_sizes_round_trip(self) -> None:
        before = lt.set_sizes(page(), (1,), [320, 480])
        assert lt.from_dict(lt.to_dict(before), KNOWN).children[1].sizes == (320, 480)

    def test_an_unknown_block_rejects_the_whole_tree(self) -> None:
        # Strict about where a block lives: guessing would silently move one.
        assert lt.from_dict(lt.to_dict(page()), KNOWN - {"skills"}) is None

    def test_a_block_named_twice_rejects_the_whole_tree(self) -> None:
        doubled = lt.to_dict(Split(HORIZONTAL, (Leaf(("skills",)), Leaf(("skills",)))))
        assert lt.from_dict(doubled, KNOWN) is None

    def test_bad_sizes_degrade_instead_of_rejecting(self) -> None:
        # Lenient about the cosmetic numbers: this only costs remembered widths.
        model = lt.to_dict(page())
        model["children"][1]["sizes"] = ["wide", "narrow"]
        assert lt.from_dict(model, KNOWN).children[1].sizes == ()

    def test_a_silly_active_index_degrades_instead_of_rejecting(self) -> None:
        model = lt.to_dict(lt.merge_into(page(), "skills", "abilities"))
        assert lt.from_dict(model, KNOWN) is not None

    def test_garbage_is_refused(self) -> None:
        assert lt.from_dict(None, KNOWN) is None
        assert lt.from_dict({"type": "leaf", "keys": []}, KNOWN) is None
        assert (
            lt.from_dict({"type": "split", "orientation": "diagonal", "children": []}, KNOWN)
            is None
        )
        assert lt.from_dict({"type": "cell", "keys": ["skills"]}, KNOWN) is None


class TestMigrationFromV7:
    def v7(self, **overrides: object) -> dict:
        model = {
            "version": 7,
            "instances": [],
            "rows": [list(row) for row in DEFAULT_ROWS],
            "floating": {},
            "hidden": [],
            "pinned": {
                "edge": "right",
                "lines": [["dice"], ["scene"]],
                "align": "fill",
                "sizes": [200, 100],
                "line_sizes": [[200], [100]],
                "extent": 320,
            },
        }
        model.update(overrides)
        return model

    def test_the_rows_become_the_page(self) -> None:
        body = lt.migrate_v7(self.v7(), KNOWN)
        assert lt.keys(lt.from_dict(body["page"], KNOWN)) == [k for r in DEFAULT_ROWS for k in r]

    def test_a_vertical_strips_lines_stack(self) -> None:
        body = lt.migrate_v7(self.v7(), KNOWN)
        region = lt.from_dict(body["region"]["root"], KNOWN)
        assert region == Split(VERTICAL, (Leaf(("dice",)), Leaf(("scene",))))

    def test_a_bottom_strips_lines_sit_side_by_side(self) -> None:
        # Along a bottom strip the lines split its length rather than stacking.
        pinned = self.v7()["pinned"] | {"edge": "bottom"}
        body = lt.migrate_v7(self.v7(pinned=pinned), KNOWN)
        region = lt.from_dict(body["region"]["root"], KNOWN)
        assert region == Split(HORIZONTAL, (Leaf(("dice",)), Leaf(("scene",))))

    def test_two_blocks_on_one_line_sit_across_it(self) -> None:
        pinned = self.v7()["pinned"] | {"lines": [["dice", "scene"]]}
        body = lt.migrate_v7(self.v7(pinned=pinned), KNOWN)
        region = lt.from_dict(body["region"]["root"], KNOWN)
        assert region == Split(HORIZONTAL, (Leaf(("dice",)), Leaf(("scene",))))

    def test_the_strips_thickness_survives(self) -> None:
        assert lt.migrate_v7(self.v7(), KNOWN)["region"]["extent"] == 320

    def test_hidden_and_floating_blocks_are_carried_over_untouched(self) -> None:
        rows = [row for row in DEFAULT_ROWS if row != ["skills"]]
        floating = {"conditions": {"x": 1, "y": 2, "w": 3, "h": 4, "on_top": True}}
        model = self.v7(
            rows=[list(r) for r in rows if r != ["conditions"]],
            hidden=["skills"],
            floating=floating,
        )
        body = lt.migrate_v7(model, KNOWN)
        assert body["hidden"] == ["skills"]
        assert body["floating"] == floating

    def test_anchors_come_across_when_they_are_there(self) -> None:
        anchors = {"skills": {"neighbour": "conditions", "in_row": False, "before": False}}
        body = lt.migrate_v7(self.v7(hidden_anchors=anchors), KNOWN)
        assert body["hidden_anchors"] == anchors

    def test_a_layout_with_no_anchors_still_migrates(self) -> None:
        assert "hidden_anchors" not in lt.migrate_v7(self.v7(), KNOWN)

    def test_the_body_carries_no_version_of_its_own(self) -> None:
        # The caller stamps it, so this stays a pure translation.
        assert "version" not in lt.migrate_v7(self.v7(), KNOWN)

    def test_a_layout_of_another_version_is_not_migrated(self) -> None:
        assert lt.migrate_v7(self.v7(version=6), KNOWN) is None
        assert lt.migrate_v7(self.v7(version=8), KNOWN) is None

    def test_a_block_in_two_places_is_refused(self) -> None:
        pinned = self.v7()["pinned"] | {"lines": [["skills"]]}
        assert lt.migrate_v7(self.v7(pinned=pinned), KNOWN) is None

    def test_a_block_this_version_no_longer_has_is_refused(self) -> None:
        assert lt.migrate_v7(self.v7(), KNOWN - {"conditions"}) is None

    def test_garbage_is_refused(self) -> None:
        assert lt.migrate_v7({}, KNOWN) is None
        assert lt.migrate_v7(self.v7(rows="everything"), KNOWN) is None
        assert lt.migrate_v7(self.v7(rows=[["abilities", 7]]), KNOWN) is None

    def test_a_layout_written_before_the_strip_existed_still_migrates(self) -> None:
        model = self.v7()
        del model["pinned"]
        body = lt.migrate_v7(model, KNOWN)
        assert body["region"]["root"] is None
        assert body["region"]["edge"] == "right"


class TestMovingAWholeCell:
    """A tab group is a cell, and a cell has to be movable as one.

    Before this, the only way to move a group was to drag every tab out and merge
    them back together at the far end — the same arrangement reached through four
    intermediate ones nobody asked for, each of which the user could see.
    """

    def grouped(self) -> Split:
        """The default page with Conditions and Skills merged into one cell."""
        return lt.as_page(lt.merge_into(page(), "skills", "conditions"))

    def test_the_cell_arrives_whole(self) -> None:
        moved = lt.move_leaf(self.grouped(), ["conditions", "skills"], "abilities", "left")

        assert lt.leaf_for(moved, "conditions") == lt.leaf_for(moved, "skills")
        assert lt.keys(lt.leaf_for(moved, "skills")) == ["conditions", "skills"]

    def test_it_lands_on_the_side_it_was_asked_for(self) -> None:
        moved = lt.move_leaf(self.grouped(), ["conditions", "skills"], "abilities", "left")
        row = lt.at(moved, (1,))

        assert isinstance(row, Split) and row.orientation == HORIZONTAL
        assert lt.keys(row) == ["conditions", "skills", "abilities", "resistances"]

    def test_the_row_it_left_goes_with_it(self) -> None:
        moved = lt.move_leaf(self.grouped(), ["conditions", "skills"], "abilities", "left")

        assert lt.keys(moved).count("conditions") == 1
        assert len(moved.children) == 2  # the base row, and the one it joined

    def test_which_tab_was_showing_travels_with_it(self) -> None:
        """A group put down showing a different block than it was picked up
        showing is a small thing that feels like a bug every time."""
        start = lt.as_page(lt.set_active(self.grouped(), "conditions"))
        assert lt.leaf_for(start, "skills").active == 0

        moved = lt.move_leaf(start, ["conditions", "skills"], "abilities", "right")

        assert lt.leaf_for(moved, "skills").active == 0

    def test_dropping_a_group_on_itself_does_nothing(self) -> None:
        start = self.grouped()
        assert lt.move_leaf(start, ["conditions", "skills"], "skills", "left") == start

    def test_a_target_that_is_not_there_is_refused(self) -> None:
        start = self.grouped()
        assert lt.move_leaf(start, ["conditions", "skills"], "nobody", "left") == start

    def test_keys_that_are_not_one_whole_cell_are_refused(self) -> None:
        """Half a group is not a cell, and moving one would silently split it."""
        start = self.grouped()
        assert lt.move_leaf(start, ["skills"], "abilities", "left") == start
        assert lt.move_leaf(start, ["conditions", "abilities"], "base_info", "left") == start

    def test_it_can_be_given_a_row_of_its_own(self) -> None:
        moved = lt.move_leaf_to_row(self.grouped(), ["conditions", "skills"], 0)

        assert lt.keys(moved.children[0]) == ["conditions", "skills"]
        assert len(moved.children) == 3

    def test_a_row_index_past_the_row_it_vacated_is_re_measured(self) -> None:
        """The drop names a seam in the page it was looking at, and removing the
        cell can take a row out from under that seam."""
        moved = lt.move_leaf_to_row(self.grouped(), ["conditions", "skills"], 3)

        assert lt.keys(moved.children[-1]) == ["conditions", "skills"]
        assert len(moved.children) == 3

    def test_a_group_merged_into_a_block_makes_one_group_of_everything(self) -> None:
        merged = lt.merge_leaf_into(self.grouped(), ["conditions", "skills"], "abilities")

        leaf = lt.leaf_for(merged, "abilities")
        assert lt.keys(leaf) == ["abilities", "conditions", "skills"]

    def test_merging_keeps_the_arriving_group_showing_what_it_showed(self) -> None:
        start = lt.as_page(lt.set_active(self.grouped(), "skills"))
        assert lt.leaf_for(start, "skills").active == 1

        merged = lt.merge_leaf_into(start, ["conditions", "skills"], "abilities")

        leaf = lt.leaf_for(merged, "abilities")
        assert leaf.keys[leaf.active] == "skills"

    def test_merging_a_group_into_one_of_its_own_blocks_does_nothing(self) -> None:
        start = self.grouped()
        assert lt.merge_leaf_into(start, ["conditions", "skills"], "skills") == start

    def test_inserting_a_whole_cell_beside_nothing_appends_a_row(self) -> None:
        arriving = Leaf(("dice", "scene"))
        grown = lt.insert_node_beside(page(), arriving, "nobody", "left")

        assert grown.children[-1] == arriving

    def test_inserting_into_an_empty_page_is_the_cell_itself(self) -> None:
        arriving = Leaf(("dice", "scene"))
        assert lt.insert_node_beside(None, arriving, "anything", "left") == arriving

    def test_a_bad_side_is_still_refused(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            lt.insert_node_beside(page(), Leaf(("dice",)), "skills", "sideways")

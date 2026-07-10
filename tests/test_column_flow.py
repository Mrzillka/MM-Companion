"""Unit tests for the panel-count / balanced-split helpers (no Qt needed)."""

from __future__ import annotations

from mm_companion.ui.sections.column_flow import column_count, even_split


class TestColumnCount:
    def test_unknown_width_yields_one_panel(self) -> None:
        # Before the first resize the width is 0; a single panel is the safe paint.
        assert column_count(0, 200, 6, 10) == 1
        assert column_count(-50, 200, 6, 10) == 1

    def test_single_item_never_splits(self) -> None:
        assert column_count(10_000, 200, 6, 1) == 1
        assert column_count(10_000, 200, 6, 0) == 1

    def test_grows_with_available_width(self) -> None:
        # min 200, spacing 6: one panel needs 200, two need 406, three need 612.
        assert column_count(200, 200, 6, 10) == 1
        assert column_count(405, 200, 6, 10) == 1
        assert column_count(406, 200, 6, 10) == 2
        assert column_count(612, 200, 6, 10) == 3

    def test_capped_by_item_count(self) -> None:
        # Plenty of width, but only two items → at most two panels.
        assert column_count(10_000, 200, 6, 2) == 2

    def test_shrinks_as_min_col_width_rises(self) -> None:
        # Same width; wider content (a longer entry) means fewer panels fit.
        assert column_count(800, 200, 6, 10) == 3
        assert column_count(800, 260, 6, 10) == 3
        assert column_count(800, 400, 6, 10) == 1


class TestEvenSplit:
    def test_empty(self) -> None:
        assert even_split([], 3) == [[], [], []]

    def test_returns_exactly_groups_buckets(self) -> None:
        assert len(even_split([1, 1, 1, 1], 2)) == 2
        assert len(even_split([1, 1], 4)) == 4

    def test_order_preserved_and_contiguous(self) -> None:
        buckets = even_split([1, 1, 1, 1, 1, 1], 3)
        flat = [i for bucket in buckets for i in bucket]
        assert flat == list(range(6))  # every index once, in order

    def test_equal_weights_balanced(self) -> None:
        assert even_split([1, 1, 1, 1, 1, 1], 2) == [[0, 1, 2], [3, 4, 5]]
        assert even_split([1, 1, 1, 1, 1, 1], 3) == [[0, 1], [2, 3], [4, 5]]

    def test_uneven_weights_minimise_tallest(self) -> None:
        # A tall block up front should sit alone against the lighter remainder.
        buckets = even_split([5, 1, 1, 1, 1], 2)
        sums = [sum(idx_to_weight(b, [5, 1, 1, 1, 1])) for b in buckets]
        assert buckets == [[0], [1, 2, 3, 4]]
        assert max(sums) == 5

    def test_blocks_never_split(self) -> None:
        # Each index lands in exactly one bucket.
        buckets = even_split([3, 2, 4, 1], 2)
        flat = sorted(i for bucket in buckets for i in bucket)
        assert flat == [0, 1, 2, 3]

    def test_fewer_blocks_than_groups_leaves_trailing_empty(self) -> None:
        assert even_split([1, 1], 4) == [[0], [1], [], []]


def idx_to_weight(indices: list[int], weights: list[int]) -> list[int]:
    return [weights[i] for i in indices]

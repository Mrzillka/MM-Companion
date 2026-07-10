"""Helpers for laying a list out across a variable number of side-by-side panels.

Both the Skills and Advantages blocks render their rows across several
side-by-side tables. The number of panels is not fixed: it adapts to the block's
current width so a wide block shows more columns and a narrow one shows fewer,
and it never lets a panel get narrower than the content needs (so nothing is
clipped). These two pure functions carry that logic, kept free of Qt state so
they are unit-testable:

- :func:`column_count` decides how many panels fit a given width.
- :func:`even_split` divides an ordered list of weighted blocks into that many
  near-equal-height buckets without reordering or splitting a block.
"""

from __future__ import annotations


def column_count(available_width: int, min_col_width: int, spacing: int, item_count: int) -> int:
    """How many panels of at least *min_col_width* fit into *available_width*.

    ``n`` panels laid side by side with *spacing* between them occupy
    ``n * min_col_width + (n - 1) * spacing``; solving for the largest ``n`` that
    still fits gives ``(available_width + spacing) // (min_col_width + spacing)``.
    The result is clamped to at least one and never more than *item_count* (an
    empty list still yields one panel). A zero/negative width or min width falls
    back to a single panel, so the first paint — before any real geometry — is
    safe; the true value lands on the first ``resizeEvent``.
    """

    if item_count <= 1 or available_width <= 0 or min_col_width <= 0:
        return 1
    fit = (available_width + spacing) // (min_col_width + spacing)
    return max(1, min(item_count, int(fit)))


def even_split(weights: list[int], groups: int) -> list[list[int]]:
    """Partition indices ``0..len(weights)-1`` into *groups* ordered buckets.

    Order is preserved and a block (one weight) is never split, so this suits
    dividing a column of variable-height rows into near-equal-height panels. The
    split minimises the tallest bucket (the classic linear-partition problem)
    via a small dynamic program — a faithful N-way generalisation of the exact
    two-way split the skills block used before. Exactly *groups* buckets are
    returned; trailing ones are empty only when there are fewer blocks than
    buckets.
    """

    groups = max(1, groups)
    result: list[list[int]] = [[] for _ in range(groups)]
    n = len(weights)
    if n == 0:
        return result

    parts = min(groups, n)
    prefix = [0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + weights[i]

    inf = float("inf")
    # dp[p][i] = smallest achievable tallest-bucket when the first i blocks are
    # split into p contiguous parts; choice[p][i] records the last divider.
    dp = [[inf] * (n + 1) for _ in range(parts + 1)]
    choice = [[0] * (n + 1) for _ in range(parts + 1)]
    for i in range(1, n + 1):
        dp[1][i] = prefix[i]
    for p in range(2, parts + 1):
        for i in range(p, n + 1):
            best, best_j = inf, p - 1
            for j in range(p - 1, i):
                candidate = max(dp[p - 1][j], prefix[i] - prefix[j])
                if candidate < best:
                    best, best_j = candidate, j
            dp[p][i], choice[p][i] = best, best_j

    # Walk the dividers back from (parts, n) to recover each contiguous bucket.
    i = n
    for p in range(parts, 0, -1):
        j = choice[p][i]
        result[p - 1] = list(range(j, i))
        i = j
    return result

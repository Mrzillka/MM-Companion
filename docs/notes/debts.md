# Accepted debts

Work that has been agreed and **not yet done**. It is here rather than in an issue
tracker for the reason the rest of `docs/notes/` exists: the useful half of a debt is
*why* it is owed and what the current shape gets wrong, and that is worth reading before
touching the area rather than after.

Take one off this list by doing it, and delete its entry in the same commit.

## The Powers and Equipment cards should be restated, not rebuilt

`PowersSection._rebuild_list` and `EquipmentSection._rebuild_cards` still destroy
every card and make it again on every redraw. Everything cheaper has been done
around them — the build math is gathered once per pass (`core/rules/build_cache.py`),
the merged catalogs are memoized, and the redraw itself is coalesced to once a turn
(`BlockDescriptor.coalesces`), so a backlog of spin-box steps now collapses into one.
What is left is that the one redraw costs ~45 ms on a furnished sheet, nearly all of
it Qt building and laying out widgets that mostly did not change.

The Scene board shows the shape of the fix: `SceneBoard._rebuild` keeps the card
already showing a ref and restates it through `SceneCard.set_entry`. The other two
cannot do that yet because a card's *content* and its *widgets* are made in one pass —
`_make_card` reads a dozen derived values as it builds — so there is nothing to compare
a node against to decide the card can be kept.

So the work is: split each card builder into "derive what this card says" and "build
the widgets that say it", key the cards by node/item id, and rebuild only where the
derived record changed. The risk is the reason it is not done: a missed input means a
card showing a **stale number**, which is worse than a slow one. Whatever signature is
chosen needs a differential test — for a battery of model mutations, assert the reused
section renders identically to one built from scratch — and that test is the deliverable
as much as the reuse is.

Not urgent. The lag that prompted all of this was the window flash and the repeated
gathers, both fixed; this is the remainder, and it no longer shows up as a dropped
frame during ordinary editing.

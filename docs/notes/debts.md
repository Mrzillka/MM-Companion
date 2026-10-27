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

## The rest of the Impress action, and of area attacks

Pieces of the two actions on the targeting layer were left for later; see [the actions
notes](actions.md) for the shape they would plug into.

- **A Hero Point inside an area's table.** Every NPC line is rolled once by the server
  and applied; a re-roll would have to re-resolve that row and take back the conditions
  it already applied. Players are not affected — they roll from a request, and the
  Reroll button works on their own card as on any roll. (A reroll of the attack against
  the primary *is* followed, but only until the GM resolves.)
- **Areas that resolve to neither conditions nor a heal.** Area Nullify (an opposed
  check against a power's rank) and Move Object are not offered on the area line's
  resolve path beyond what their ladder says; the table shows the degree and the GM
  applies the rest. Each wants its own stage-two rule, the way Healing got
  `checkOutcomes`. (Weaken is an Affliction preset here, so it already lands its
  conditions — scoped to its trait by the Affliction's `scope` field.)
- **Selective, cover from the origin, the size rule.** Who the area catches is the
  attacker's declaration and the GM's edit — Selective is honoured by people, not code;
  cover is a per-creature combo the GM sets rather than geometry; and "three size ranks
  larger makes every attack an area" (PDF p58) is the free area switch the player
  flips, not a gate the app checks.

## The minion rules left out

Minions are in (see [the minions notes](minions.md)). These rules about them are not:

- **Takedown** (PDF p95). Downing a minion grants a free attack on another; at rank 2
  that target need not be adjacent. It wants the attack to know its target, which no
  single-target attack here does yet.
- **Menaces** (PDF p25): bonus reactions per turn, and spending a commanded minion in
  place of a plot complication. There is no turn economy for either to hang off.
- **Assess** (PDF p86) counts a group of identical minions as one opponent. Nothing
  rolls Assess against a group yet.
- **Summoned and advantage minions as creatures.** A Summon's or a Minion advantage's
  build is still a build, not a creature on the board. Sending one to the GM window
  should set `minion` on it unless the Summon is Heroic.
- **A minion's 20 against another minion** is suppressed too, since a roll does not know
  its target when it is thrown.
- **Request-mode Impress against minions** is not routine: each target resists from the
  card against the rolled total, minions included. The routine DC (and the better of the
  two, as a resolved Impress uses) wants the request card to know who is a minion.

## End of turn does one thing so far

A creature's card on the GM's board has an **End turn** item. All it does today is
give every impression the GM's table put on that creature a new resistance check
against its original DC (the book's "overcoming impressions"), and take off the ones
that are made. It is the seam the rest of the turn economy should hang off:

- **Other end-of-turn checks.** A creature Stunned by Damage makes a DC 10 Stamina
  check to shake it; an Affliction is overcome with its `overcomeBy` resistance;
  Suffocation forces a Fortitude check each turn. Each wants its rule as data (a
  per-condition "check to end" record) and the button to roll them all.
- **What it remembers lives in the GM's app.** The impressions to shake off are kept in
  memory by `ImpressController` (`_lingering`), keyed by scene ref, from impressions the
  table *resolved* — a request-only one, or a condition put on by hand, is not
  remembered. Restarting the GM's app forgets them. The debt's earlier note suggested a
  per-condition record on `AppliedCondition`, written by `targeting.apply_rung`, which
  would survive a restart and travel with a player's sheet.
- **There is no turn tracker.** The GM ends each creature's turn by hand; initiative
  order is on the board, but nothing walks it.

## PySide6 is capped below 6.12

`pyproject.toml` pins `PySide6>=6.6,<6.12`. The cap went in on 2026-10-10, when
PySide6 6.12.0 was released and CI — which had passed on unchanged `develop` two days
before with 6.11.2 — failed on every Python version in two ways:

- **`tests/test_input_arrow_columns.py`, 32 failures** under the Windows and Fusion
  styles: a spin box's edit field overlaps its up arrow by one pixel. Those tests guard
  the fix for arrows that paint but take no clicks (`theme.arrow_columns` measures the
  style's arrow column so the stylesheet can give it back as padding), so this is
  likely a real regression for users, not a test artefact.
- **`Fatal Python error: Aborted` while garbage-collecting** in `test_pinned_panel.py`
  on 3.10 and 3.11, at a different test each run.

Both reproduce only on 6.12 (a local 6.11.1 passes everything), so the work is: install
6.12 locally, find what moved the arrow column and what the collector is tearing down
out of order, fix both, and lift the cap. Until then the release builds stay on 6.11
too, because they install from the same file.

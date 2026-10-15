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

- **Shaking an impression off.** A condition an impression imposed should get a new
  resistance check against the original DC at the end of each of the target's turns.
  The conditions land, but nothing remembers the DC or prompts the save — there is no
  turn-economy layer to hang it on yet (the conditions notes put recovery out of scope
  for the same reason). The likely home is a per-condition "save to end" record on
  `AppliedCondition`, written by `targeting.apply_rung`.
- **A Hero Point inside an area's table.** Every NPC line is rolled once by the server
  and applied; a re-roll would have to re-resolve that row and take back the conditions
  it already applied. Players are not affected — they roll from a request, and the
  Reroll button works on their own card as on any roll.
- **Areas that do not resolve to conditions.** Area Weaken, Nullify (an opposed check),
  Healing (beneficial, no Dodge) and Move Object are not offered on the area line's
  resolve path beyond what their ladder says; the table shows the degree and the GM
  applies the rest. Each wants its own stage-two rule.
- **A heavy weapon's two ranks.** The Rocket Launcher's catalog entry prints a primary
  rank of 10 and an area rank of 7 (`primaryTargetDamageRank` / `areaDamageRank` under
  `implementation`); the area line uses the build's one rank for both. A data hook for
  "the primary takes a different rank" belongs on the area modifier's config.
- **Selective, cover from the origin, the size rule.** Who the area catches is the
  attacker's declaration and the GM's edit — Selective is honoured by people, not code;
  cover is a per-creature combo the GM sets rather than geometry; and "three size ranks
  larger makes every attack an area" (PDF p58) is the free area switch the player
  flips, not a gate the app checks.
- **Linked areas dodge against the lead's DC.** A power whose several effects are all
  areas makes one Dodge check against the first effect's rank, and a player is asked
  for the further saves as separate requests whose label gives both DCs.
- **Counting earlier attempts.** "Each previous attempt to Impress this scene" is a
  slider the GM sets. The window could count attempts per target since the last New
  scene and preset it.

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

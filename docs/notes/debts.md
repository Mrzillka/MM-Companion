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
  that target need not be adjacent. Aimed attacks (see [the actions
  notes](actions.md#aimed-attacks)) now know their target, so the controller can see a
  minion go down to the attack; offering the free attack is what is left.
- **Menaces** (PDF p25): bonus reactions per turn, and spending a commanded minion in
  place of a plot complication. There is no turn economy for either to hang off.
- **Assess** (PDF p86) counts a group of identical minions as one opponent. Nothing
  rolls Assess against a group yet.
- **Summoned and advantage minions as creatures.** A Summon's or a Minion advantage's
  build is still a build, not a creature on the board. Sending one to the GM window
  should set `minion` on it unless the Summon is Heroic, and set the `summoned` tag,
  which exists already: the GM sets it by hand today (see [the minions
  notes](minions.md#the-summoned-tag)).
- **A minion's 20 against another minion** is suppressed too, since a roll does not know
  its target when it is thrown.
- **Request-mode Impress against minions** is not routine: each target resists from the
  card against the rolled total, minions included. The routine DC (and the better of the
  two, as a resolved Impress uses) wants the request card to know who is a minion.

## End of turn does one thing so far

A creature's card on the GM's board has an **End turn** item. It gives every
impression the GM's table put on that creature a new resistance check against its
original DC (the book's "overcoming impressions"), and rolls every condition whose
`recoveryCheck` is made at the end of a turn (Dazed, Stunned). It is the seam the rest
of the turn economy should hang off:

- **Other end-of-turn checks.** An Affliction is overcome with its `overcomeBy`
  resistance against its own DC; Suffocation forces a Fortitude check each turn.
- **It does not know why a condition is on.** Dazed and Stunned are rolled off with
  DC 10 Stamina because their record says so, and the record means a *damage-caused*
  one (`recoveryCheck.condition`). One an impression put on is recognised and shaken
  off as the impression instead; one an Affliction put on is not, and End turn offers
  it the Stamina check as well. The fix is the per-condition record below: written
  when an impression or an Affliction lands a condition, it says how that condition is
  shaken off, and a condition with none is the damage-caused one.
- **What it remembers lives in the GM's app.** The impressions to shake off are kept in
  memory by `ImpressController` (`_lingering`), keyed by scene ref, from impressions the
  table *resolved* — a request-only one, or a condition put on by hand, is not
  remembered. Restarting the GM's app forgets them. The debt's earlier note suggested a
  per-condition record on `AppliedCondition`, written by `targeting.apply_rung`, which
  would survive a restart and travel with a player's sheet.
- **There is no turn tracker.** The GM ends each creature's turn by hand; initiative
  order is on the board, but nothing walks it.

## Aid, Feint and Trick: what is left

Built in phase 8 of the combat maneuvers (see [the actions notes](actions.md#aid-feint-and-trick)).

- **Off the air they are greyed.** With no session there is no Scene to pick the target
  off and no GM to resolve it. An offline version would roll the check and leave a
  "Resist Feint vs. DC" request card, as an offline Impress does.
- **The opposing conditions count in aimed attacks only.** An unaimed attack (the DC
  box standing for the Defense), an area's primary and a team attack's entries neither
  count nor spend them; the bearer takes them off by hand.
- **They are matched by name.** The parameter is the name the GM's board showed when
  the action landed; renaming a creature (or a namesake's number shifting) leaves one
  that never matches.
- **Feint's "corresponding" attack bonus.** The target resists with the better of both
  combat skills, since an attack does not record whether it is close or ranged.

## Grab, Trip, Disarm, Smash, Escape and Block: what is left

Built in phase 9 of the combat maneuvers (see [the actions
notes](actions.md#grab-trip-disarm-smash-and-escape)).

- **Off the air they are greyed**, as Aid, Feint and Trick are (Block is not: it is a
  self action).
- **Only the first hold.** Improving a hold with a later Grab (cumulative degrees, no
  attack check; a failed one frees the target), the size rules (a target three sizes
  smaller; one too big for more than a partial hold), and carrying, squeezing,
  throwing or wielding a held creature are the GM's to run by hand.
- **The advantages are not read.** Improved Grab, Improved Hold (−5 to Escape),
  Improved Trip, Improved Disarm and Improved Smash change nothing yet.
- **"Close" is not checked.** A Grab, a Trip or a Block may be made with any attack the
  picker offers; a spec does not record its range.
- **Two holds share one bundle.** Holding (Ana) and Holding (Bo) give Hindered and
  Vulnerable the same provenance, so letting go of one takes both bundles' members.
- **The GM's judge does not block.** A Blocking creature's block is rolled by hand with
  the attacker's result in the DC box (the user's call, 2026-11-09).
- **Narrated outcomes stay narrated.** A disarmed item, a smashed object's Damage and a
  trip's extra distance are said in the history; nothing tracks them.

## Combat maneuvers: what is left overall

The feature as a whole (`docs/plans/combat-maneuvers.md`; the two sections above hold
the targeted actions' own). Each of these was seen and left on purpose.

- **No turn clock.** Temporary conditions last "until the start of your next turn", and
  the player or the GM takes them off with **Clear temporary**. End turn does not clear
  them (the user's call: they end at the *start* of the creature's next turn). A turn
  tracker would clear a creature's temporaries as its turn starts.
- **Prone's ±5 on attacks never reaches an attack roll.** `condition_attack_mods`
  (close +5 against a prone target, ranged −5, and the prone attacker's own −5) is read
  by the Conditions block's display only; `power_rolls` folds in the amount mods
  (`condition_attack_check_mod`) but not these. Found in phase 1; out of the feature's
  scope.
- **Charge's −2 is not in the maneuvers' caps.** `maneuver_values` reads the attack's
  modifier before the lines' fixed shifts, so a +2 attack charged (+0) can still be
  doubled by All-Out to +4.
- **Recover works "once per scene"** says its tooltip; nothing counts it.
- **Aim's "lost if your next standard action is not that attack"** is not tracked. The
  line stays (greyed) through a roll that is not an attack, and the player drops it with
  its ✕.
- **Accurate and Power** change only the attack being made. By the book they last until
  the start of the next turn, which matters only for a second attack in one turn.
- **Every line grows the roller** by a row: the empty lane takes no room (the user's
  call, 2026-11-09; the Actions button moved to the toolbar above the lane on
  2026-11-10, so it no longer moves). Accepted.
- **No dot on the System block.** Nothing temporary moves Initiative or Speed yet; add
  a `StatMark` there when something does.

## PySide6 is capped below 6.12, and 6.11.3 is excluded

`pyproject.toml` pins `PySide6>=6.6,<6.12,!=6.11.3`, so CI and the release builds get
6.11.2. Two problems, which arrived separately:

- **The cap** went in on 2026-10-10, when PySide6 6.12.0 was released and CI failed on
  every Python version: the arrow-column tests below, and a **`Fatal Python error:
  Aborted` while garbage-collecting** in `test_pinned_panel.py` on 3.10 and 3.11, at a
  different test each run. The abort is still unexplained.
- **The exclusion** went in on 2026-10-29, after `develop`'s CI went red on 2026-10-28:
  PySide6 6.11.3 had arrived, with the same `test_input_arrow_columns.py` failures (32
  on CI, 64 on Windows, which has two more styles).

**The arrow failures are a Qt bug, not ours.** Qt's "QStyleSheetStyle: Honor
horizontally aligned QSpinBox buttons" (qtbase `7d02beaf`, in 6.11.3 and 6.12.0)
rewrote how `SC_SpinBoxEditField` is cut around the buttons. With `NoButtons` it cuts
at an empty rect at x=0, so **under any stylesheet a button-less spin box gets a text
field 1px wide**. Every `make_spin_box(buttons=False)` box and every locked spin box is
like that, so on 6.11.3 a locked sheet shows 31 empty fields, under Classic too. The
same rewrite also overwrites the content rect's edges, which is why our
`padding-right` stopped doing anything. Arrows still take clicks on every version.

Qt fixed it in "QStylesheetStyle: fix spinbox padding" (qtbase `6cc8253372`,
QTBUG-151308), picked to **6.12 only**: 6.12.1 will have it, and no 6.11 will. With
the fix, a button-less box gets its whole content rect again (so the arrowless
`padding-right: 0` rule matters again), but a box *with* buttons still has its edit
field end **on** the arrow's first pixel (`r.setRight(minX)`), whatever padding the
sheet states.

**To lift the cap:** once PySide6 6.12.1 is out, install it and run
`tests/ui/widgets/test_input_arrow_columns.py`. Expect the two "does not sit under its
text" tests to fail by exactly that 1px. Loosen them to allow an overlap of at most one
column, keeping the click test as the real proof. Then chase the garbage-collection
abort, and move the pin to `>=6.6,<6.13,!=6.11.3,!=6.12.0`.

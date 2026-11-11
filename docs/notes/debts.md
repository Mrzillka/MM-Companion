# Accepted debts

Work that has been agreed and **not yet done**. It is here rather than in an issue
tracker for the reason the rest of `docs/notes/` exists: the useful half of a debt is
*why* it is owed and what the current shape gets wrong, and that is worth reading before
touching the area rather than after.

Take one off this list by doing it, and delete its entry in the same commit.

## Before release

Work kept for the stable **1.0 release**. Ordinary releases go out without it; pick
it up when 1.0 is being planned (see [the road to 1.0](road-to-1.0.md)).

### Sign the Windows installer through SignPath Foundation

The installer is unsigned, so SmartScreen warns the first time someone runs it. The
chosen route (2026-11-11) is the **SignPath Foundation**, which signs open-source
projects for free. The alternatives were Azure Trusted Signing (about $10 a month,
identity check, only some countries) and a classic certificate (about $200–400 a
year, with the key on a hardware token, which is awkward in CI). The terms below were
read at <https://signpath.org/terms> on 2026-11-11, when the page was marked as a
draft, so read them again before applying.

Signing does not remove the warning at once. SmartScreen builds trust in a
signature as people download it, so the warning fades over the first releases.

**What we accept by choosing it.** The certificate belongs to SignPath, so Windows
shows **"SignPath Foundation"** as the publisher, not us. Every signing request needs
a manual approval in SignPath, so a release waits for that click. Only builds made by
GitHub Actions can be signed, so installers built locally by the `build-installer`
skill stay unsigned. SignPath may pause the signing or revoke the certificate without
notice if it thinks a rule was broken, and it decides applications at its own
discretion, with no appeal.

**Ask SignPath first.** Two things could stop the application, so settle them before
doing the repo work:

- **The game data's licence.** The terms require "an OSI-approved Open Source license
  … for all components". The code is MIT and PySide6/Qt are LGPL, which is fine, but
  `src/mm_companion/data/` is Open Game Content under the **OGL 1.0a**, which is not an
  OSI licence, and it ships inside the installer. The terms say nothing about content
  that is not code, so ask whether it is acceptable.
- **Reputation.** For programs (not libraries) SignPath wants "a certain verifiable
  reputation". A project with a handful of users may be told to come back later. Apply
  early if the 1.0 installer must be signed at launch; otherwise apply once the launch
  has brought users.

The terms say nothing about which countries are eligible. SignPath is based in
Austria, so ask in the same message if that could matter.

**Then the repo work:**

1. **A "Code signing policy" section.** The words "Code signing policy" must appear
   on the home page and the download page: the README, and the release notes the
   `make-release` skill writes into `docs/releases/`. The section states "Free code
   signing provided by SignPath.io, certificate by SignPath Foundation", lists the
   roles (authors, reviewers, approvers, which today are all the maintainer) and links
   the privacy policy, [`PRIVACY.md`](../../PRIVACY.md).
2. **Two-factor login** on both the GitHub account and the SignPath account; the terms
   require it for every team member.
3. **Sign in `release.yml`.** Split the Windows job so signing happens in two places:
   build with PyInstaller, then send `MM-Companion.exe` and the portable exe to SignPath
   (its GitHub action), and wait for the approval. Wrap the signed files with ISCC and
   send the finished installer for signing too, then upload it. Inno Setup's `SignTool`
   directive would also sign the uninstaller, but only if signing can run on the build
   machine. Give the waits a timeout long enough for a manual approval.

Already satisfied: the installer comes with an uninstaller, the releases are built
from the public repo by CI, and the app does not change system settings.

**Later, optional:** once the installer is signed, the updater could check its
signature on the downloaded installer as well as the SHA-256 it checks today.

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

## End of turn: what is left

A creature's card on the GM's board has an **End turn** item. It resists every
impression and overcomes every Affliction on that creature again against the original
DC, read off the `ShakeOff` each condition carries (see [the conditions
notes](conditions-and-damage.md)), and rolls every condition whose own `recoveryCheck`
is made at the end of a turn (a damage-caused Dazed or Stunned). It is the seam the rest
of the turn economy should hang off:

- **Suffocation** forces a Fortitude check each turn; nothing rolls it.
- **A third-degree Affliction** is overcome once a minute (PDF p109). End turn names it
  and rolls nothing; the GM rolls it by hand when the minute is up.
- **The Affliction modifiers that change overcoming** are not read: Progressive (a
  failed check adds a degree), Cumulative, Increasing / Decreasing Difficulty (±1 DC per
  check), Instant Recovery, Prolonged (a check per scene), Fatal (Dying on a failed
  third-degree check). Increasing Difficulty would want a check count on the record.
- **Only a rung the table resolved writes a record.** A request-only Impress, a save a
  player rolled off an unaimed attack's card, or a condition put on by hand carries none,
  so a Dazed or Stunned among them still gets the damage Stamina check.
- **Shaking one off is End turn's alone.** A chip with a record shows no 🎲 (the
  Stamina one is refused), and the Recover action's third option — a new resistance
  check against an ongoing effect (PDF p206) — is not offered.
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
  combat skills, since the feinter's check (a skill roll) does not record whether it was
  the close or the ranged one; only a power's attack check carries `attack_range`.

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
  picker offers. A power's attack spec now records its range (`RollSpec.attack_range`),
  but nothing refuses a ranged one.
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
- **A prone target's ±5 counts only where the opposing conditions do.** The prone
  attacker's own −5 on a close attack is in the roll `power_rolls` builds; the target's
  +5 close / −5 ranged is added by the GM's judge (`opposing_effects(attack_range=…)`)
  to an aimed attack and a targeted action's attack only. An unaimed attack (the DC box
  standing for the Defense), an area's primary and a team attack's entries leave it to
  the table, as the entry above says of the opposing conditions.
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

## Every source of a reroll should spend what it costs

The roller's **Reroll** (the Actions popup's Dice section, `Ctrl+Shift+R`) throws the
last roll again with the lucky die (`core.dice.reroll_d20`: 1–10 counts 10 more) and
spends nothing. A reroll at the table always has a source, and each source has a price
the app already tracks somewhere else: a **Hero Point** (the dots on Power Level &
System), the **Luck** advantage's per-adventure uses, **Determination** and the other
`usesPerAdventurePerRank` advantages' pips, and the powers that grant one. Today the
player clicks Reroll and then spends the point or the pip by hand, which is two places
to remember and one to forget.

The user's call (2026-11-10, the roller polish review): do **not** make Reroll mean
"spend a Hero Point" — that is only one of the sources. The work is to wire them all:
Reroll asks which source pays (offering only the ones with something left), spends it,
and says so in the history the way spending a hero point already writes a note. The
sources belong in game data (which advantage or power grants a reroll, and what it
costs), not in Python. A faster "reroll with a Hero Point" link beside a fresh result
was proposed and set aside until this exists.

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

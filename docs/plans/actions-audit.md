# Actions audit — 17 November 2026

Work branch: `fix/actions-audit`. Authority: local 4e Origin book, checked with
the mm-rules agent. Page numbers below are PDF pages (printed pages are two lower).
This is a live audit: **not all actions are certified correct**.

## Accepted decisions

- Readability first: explicitly say hit/miss, resistance success/failure and what
  changed. Keep arithmetic available in history. Conditional Damage caveats belong
  behind a warning icon.
- History requests load a chip; the player clicks the die. Avoid roll-request
  windows. Actions that require no roll apply immediately.
- Turn/scene restrictions are reminders, enforced by the table.
- Use Stamina for damage-caused Dazed/Stunned recovery despite the conflicting
  later Fortitude passage.
- Choose the movement actually used for Slam and speed-based Stand.
- Resolve Smash object saves and Trip extra-throw falling saves inline in history.
  GM inputs: object Toughness, existing Hits, target mass and falling mitigation.
  A plain adjacent Trip deals no falling Damage.
- Keep the existing failed Group Check degree interpretation.
- Defer full Grab/Escape to [its own plan](grab-escape-follow-up.md).
- Opposed interpretation: compare totals; ties compare modifiers, then randomize
  equal modifiers. Shift the actor's result for both natural faces, skipping zero.
  Suppress natural-20 added success for minions and synthetic faces for routine
  checks/lifted rerolls. This combined-natural algorithm is an agreed interpretation,
  not an explicit book algorithm.

## Shared changes under verification

- Final aimed-attack and targeted-action adjudication is posted to the shared
  history, rather than appearing only on the GM's local action card/notice.
- Explicit hit/miss and resistance wording; natural-face badges independent of
  mechanical added success; conditional Damage caveat behind a warning.
- Requests/follow-ups and Recover load the roller. Player declarations wait for
  the die; NPC aimed/targeted attacks prepare the GM roller.
- Feint/Trick/Grab/Trip/Disarm opposition uses both rolls, modifier tiebreaks,
  and the agreed natural-face interpretation. Their ungraded opposed rolls are
  followed by the shared adjudication, avoiding a misleading intermediate grade.
- Block offers applicable close attacks plus a basic Attack-ability check, even
  without an attack power. Its successive penalties still count settled checks.
- Feint uses the corresponding combat category and its owned skill pools.
- Aid Defense persists through attacks; expiry remains a table reminder.
- Slam/Stand select movement. Charge/Grab/Trip filter close attacks.

## One-by-one review

“Basic match” means source/code inspection found no mismatch in that part; it is
not a claim that every combination or session path has passed a runtime test.

| Action | Book | Finding / next verification |
| --- | --- | --- |
| Accurate Attack | 202 | Basic trade math matches; check combined caps and linked effects. |
| All-Out Attack | 202–203 | Basic math/temporary Defense match; check repeated attacks and stacking. |
| Defensive Attack | 202–203 | Basic math/temporary Defense match; check repeated use. |
| Power Attack | 202 | Basic math matches; check all linked follow-up ranks/DCs. |
| Finishing Attack | 203 | DC 10, critical and lethal ladder implemented; target eligibility needs review. |
| Surprise Attack | 203 | GM grants Vulnerable/Defenseless; universal temporary expiry is an app convention. |
| Aid | 202 | Attack/DC ladder matches. Defense consumption corrected; timing is a reminder. Engagement remains table-judged. |
| Aim | 202 | Two dice implemented. Reminder for same target/next standard action; target identity is not tracked. |
| Attack | 197 | Shared hit/resistance results now visible to players; check critical/graze, minions and condition combinations. |
| Block | 204 | Basic close check works without a power; successive penalties apply. Incoming result is still entered manually; tied modifiers are judged by table. No automatic interception of an incoming hit. |
| Catch | 204 | Agility/DC match; free hand, lifting and reaction constraints are reminders. |
| Charge | 204 | Attack penalty matches; close selection corrected. Movement distance/path remain table-judged. |
| Slam variants | 204 | Rank/own-impact formulas match; movement selection corrected. Verify full-speed and own-save histories. |
| Defend | 204 | Basic temporary Defense benefit matches; no die needed. |
| Disarm | 204 | Attack penalty and opposed stage present; opposition corrected. Dropped item/Catch follow-up is narrative. |
| Drop Prone | 204 | Immediate condition; verify derived posture and movement. |
| Escape | 204 | Basic Strength-based grab DC works. **Open:** actual modified Grab DC and per-hold provenance; unrelated Restrained/Bound may be removed. |
| Feint | 204 | Corresponding combat resistance and opposition corrected; expiry reminder. Verify focus/power/condition bonuses. |
| Grab | 205–206 | Close attack/opposed hold implemented. **Open:** improve existing hold, failed improvement, sustain/size rules and grabbing without holding. |
| Impress | 201 | Generic skills/conditions/minions implemented. **Open:** advantage-specific skill restrictions, per-target repeat attempts, Fascination maintenance/danger and migrating the legacy opposed-DC calculation to the agreed combined-natural algorithm. |
| Recover | 206 | Loads chosen check, immediate Defense benefit; usage/movement reminder. Stamina retained by decision. |
| Smash | 206 | Attack penalties implemented. Inline object save resolves Hit/Broken/Destroyed. The GM enters Toughness/Hits and defense exceptions; equipment damage state is not persisted. |
| Stand | 206 | Check/slow/Instant Up options match; movement selection corrected. |
| Trick | 206–207 | Opposition corrected. Next-action benefit/consumption reaches targeted checks and named Impress targets. Untargeted narrative actions remain manual. |
| Trip | 207 | Close hit/opposed/prone stages implemented. Inline optional throw calculates the distance and resolves falling Damage; the GM enters mass and final mitigated Damage rank. |
| Area Attack | 152, 88 | Dodge/rank/Evasion present. **Open:** point targeting/miss displacement, self-inclusion, emanation cover and sensing spreads. |
| Team Attack | 203–204 | Rank/helper math exists. **Open:** compatibility compares ladder category rather than actual effect identity. |
| Team Check | 20 | Basic helper-success math matches; check helper trait selection/history attribution. |
| Group Check | 20 | Half succeed/lowest success/net natural adjustment match. User retains the existing least-severe failed member degree interpretation. |
| Reroll | 17, 92 | Lifted-die formula exists. **Open:** choosing original vs new result and Luck/Hero Point accounting. |
| Ask to Roll | app feature | History-first workflow; verify own-trait localization and completion. |
| Take 10 | 17 | Routine flag and no natural badge; check opposed and minion paths. |

## Remaining discussions

- Persistent equipment damage and dropped items remain unmodelled; full Grab/Escape is separately deferred.
- Failed Block attempts count for successive penalties, as confirmed by the user.
- Request-only/free-form actions with no identified opponent require manual condition consumption.

## Validation record

- Before edits: 266 selected action/history tests passed after permitting loopback
  sockets; source inspection nevertheless found the defects above.
- Focused checks: 56 rules/targeted-follow-up tests, 137 GM/history/session tests,
  and 169 related action checks passed. Ruff and Black checks passed.
- The local full run reached the unrelated PDF-printing check, where Windows COM
  repeatedly failed with 0x80040155. Full Linux CI is required before merging.
- Do not merge or describe this audit as complete while the open findings remain.

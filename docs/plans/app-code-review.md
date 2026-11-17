# App code review

Goal: review the whole app and fix confirmed defects. Implementation branch:
`fix/roller-review`. The user authorized committing and merging into `develop`
on 2026-11-17.

## Review scope and coverage

The user confirmed on 2026-11-17: preserve documented behavior and finish this
code review; existing combat-timing debts remain feature work. This is a broad
code/behavior review, not a claim that every action is RAW-complete or that the
separate manual table-play checklist in `docs/notes/debts.md` is complete.

- [x] Roller inputs, consumption, rerolls, sequential turn checks, and recovery.
- [x] Character/settings/session/note persistence and failure reporting.
- [x] Session transport, handshake/reconnect lifecycle, history limits, snapshots,
  hub creation/capacity/rollback, and client/server shutdown.
- [x] Mod discovery/import validation, content paths, preferences, merge ordering,
  registries, and cache identity/invalidation contracts.
- [x] Updater download, cancellation, window shutdown, installer handshake,
  restart failure, startup reporting, and installer-script contract review.
- [x] Character serialization/runtime restore, core trait math, powers/equipment,
  rank shifts, PL validation/estimation, movement, and condition interactions.
- [x] Individual action source/rules checks with the mandated rules agent; player,
  GM, offline and hosted regression paths. Documented policies are preserved.
- [x] Sheet/block layout validation and migration, notes/undo lifecycle, simple/
  compact view contracts, shared widgets, locking and theme invariants.
- [x] Startup, headless entry points, package-data coverage, and CI configuration.
- [x] Final full suite, lint, formatting, dependency-direction and whitespace checks.
- [x] Real-app driver checks: final launcher, sheet, constructor, GM and sheet-demo
  rendered and visually inspected. Earlier NPC/simple-sheet/roller-lane renders
  also passed; final repeat NPC/simple-sheet renders were blocked by the automatic
  approval review usage limit. No safety rejection was made and no bypass attempted.

Integration is a local `--no-ff` merge into `develop`. No push or release was requested.

## Final validation

- **5,311 passed, 1 skipped in 20m04s; pytest exit code 0.** The skip is the
  symlink-creation test, unavailable without Windows symlink privileges.
- Ruff, Black (478 Python files), and `git diff --check` passed.
- The tested source was frozen and verified unchanged after the run: **555 files**,
  SHA-256 `dea4692956b4b8630f37408e7ca18e0becf99c139f66829699bd22a6d63bbc4c`.
- All 78 core Python modules pass the direct-import layering check; no Python
  lives under game data. No dependency or protocol-version change was introduced.
- The complete regression suite covers the final fixes below. Earlier partial and
  interrupted runs are retained in the historical record only, not as final proof.

## Confirmed fixes

1. Equal split Bonus/Penalty survived consumption because their net was already 0.
2. End turn precomputed later checks before earlier successes removed penalties.
3. A refused second NPC recovery click discarded the pending first check.
4. Direct file overwrites could destroy the last good save on a write failure.
   Whole-file writes now use unique temporary files and atomic replacement.
5. Failed sheet saves were uncaught, and saved-image references changed before
   the save succeeded. Failures now keep the sheet dirty and report an error.
6. Valid JSON with malformed character/settings/session shapes could stop the
   launcher or session picker. Invalid UTF-8 and torn roll-log lines also escaped
   the previous JSON-only error handlers.
7. Malformed update asset collections could terminate the update-check worker.
8. A long history of individually valid large roll specs made Welcome exceed the
   wire cap. The recent slice now respects the encoded byte budget as well as count.
9. Invalid/path-traversing mod ids reached the import destination; malformed
   metadata could be copied before validation or crash discovery on next startup.

10. Build-cache keys now use function identity, avoiding collisions between closures.
11. Updater assets must be basenames before any cleanup, cache lookup, or download.
12. Known rank zero is distinct from unknown rank throughout maneuver, Slam, and
    targeted-action consumers; zero-rank Damage still shifts its damage ladder.
13. Live equipment snapshots include worn state and current speed, separately from
    saved builds. NPC refresh/open retains runtime state too.
14. Failed Notes writes retain editors through close, split, undo, layout changes,
    and sheet shutdown; failures report once and retry without a zero-delay loop.
15. Update shutdown asks build editors first, respects Cancel, and lets a saved
    power reach its owning sheet before asking that sheet to save.
16. Layout restoration validates before destruction, preflights dirty Notes, and
    unregisters removed sections so restoring them builds fresh widgets.
17. Malformed player snapshots retain the GM's last valid card and report the error.
18. Malformed mod settings no longer crash startup or the Mod Manager; validated
    storage accessors filter id lists and per-mod override objects.

19. A refused hub handshake now closes its connection, allowing a corrected-secret
    retry; ordinary refused requests still retain a usable channel.
20. Null/non-text manifest display metadata is rejected before Qt receives it.
    Failed trusted-mod Python imports are recorded in the diagnostic log.

21. Client context-manager exit now closes reconnecting sessions too. An in-flight
    first dial or redial cannot adopt a connection after close, and another dial
    cannot start while the previous worker is still unwinding. Reproduced both
    the surviving backoff worker and a late redial returning the client online.

22. Pending hub creations reserve capacity, including sessions awaiting relay
    recovery, so parallel creates cannot pass the same ceiling check. Failed
    hosting rolls back the unclaimed save and returns a readable request error.

23. Cancelled update dialogs ignore queued download completions, so Cancel cannot
    later close windows or launch Setup.
24. A failed installer cancellation request keeps polling rather than stranding
    the handshake. Failure to write the next-launch note logs and still exits
    once Setup is ready. Stale handshake cleanup errors become visible UpdateError
    failures, and pending-note cleanup cannot break next-launch reporting.
25. Settings/mod restarts now use the same ordered close routine as updates,
    protecting unsaved constructors and respecting Cancel before closing owners.

26. Cancelled/failed updates restore future restart requests. Deferred requests
    from before/during the update are invalidated, so restoring restart support
    cannot run an old Settings/Mod Manager callback during handoff.
27. NPC estimated Power Level now uses each Linked power's shared attack bonus,
    matching its actual roll and validator. A rank-6 Accurate Damage + rank-8
    Affliction at shared +11 was incorrectly estimated PL 9 instead of PL 10.

28. Mod content paths reject parent traversal, absolute/drive paths and alternate
    stream syntax before import/discovery. Reads also resolve inside the mod root
    so symlinks cannot lead outside it. Nested relative content still works.

29. Critical saves now raise their measured effect rank and Damage ladder rank
    with their DC, including Finishing Attack; rank-sensitive defenses no longer
    see the pre-critical rank. The rules agent verified the rank increase on 4e
    PDF p197 (printed p195).
30. Team Attack enumerates leaf powers rather than passing a PowerGroup into
    `power_rolls`. Team bonuses update measured ranks even for Damage rank zero;
    known zero participates in compatibility rank limits instead of waiving them.
31. The broader run exposed a layout compatibility regression: excluded Notes
    instances remained in a legacy NPC layout's instance declarations after their
    frames were stripped. Those declarations are stripped with the frames now.

32. Launcher startup failures now close the splash, log, show a readable error,
    and return failure instead of terminating behind an unexplained splash.
33. Block counts commit when a prepared check settles, not on loading/abandoning;
    rerolls do not increment again. The manual DC instruction now says attacker
    result +1, because Block must beat that total (4e PDF p204/printed p202).
34. Slam uses speed after condition penalties and halves its self-damage rank
    rounding up, as Damage Resistance requires (4e PDF p199/printed p197).
35. Character parsing rejects non-integer trait ranks for saved files as well as
    peer snapshots, moving the duplicated GM-only check into the shared parser.

36. Restart checks whether its replacement process started. On failure it keeps
    a launcher visible and reports the error rather than quitting into nothing.
    Closing for restart also suppresses and invalidates queued duplicate restart requests.
37. Stand honors bought and active granted Instant Up. Advantage-gated replacement
    options are data-driven; the free Stand removes Prone without a check or Hindered.
38. The Actions-specific NPC recovery path now preserves a previous pending check
    when the roller refuses a second one, matching the condition-chip entry point.

## Historical validation record

- Full affected-tests baseline: **5,234 passed in 24m53s**. This predates fixes
  10-18 and does not validate those later changes.
- Later focused runs: 76 maneuver/self-action/targeted tests, 163 equipment/restore/
  main-window tests, 46 hosted player tests, 86 Notes/main-window tests, 240 updater/
  constructor tests, 98 Notes/layout/canvas tests, 7 malformed-snapshot/recovery
  tests, and 76 mod-manager/storage/loading tests passed.
- Ruff passed after the mod-settings fix. Black formatted the changed files.
- Later hub regression: reproduced before the fix; all 51 hub tests pass after.
  All 58 mod-manager/loading tests pass after metadata/logging changes.
- Client/server file: 129 tests passed after cancellation protection; the three
  lifecycle regressions passed again after guarding overlapping first dials.
- Hub capacity/rollback regressions reproduced before the fix; all 53 hub tests
  pass with the fixes.
- Updater failure-path regressions reproduced before fixes; 90 updater tests
  passed. The later updater/settings check passed 120 tests, including suppression
  restoration and invalidation of deferred restarts.
- Linked-PL regression reproduced before the fix; 277 power-roll, character,
  equipment, size, and area tests pass after it, including highest/lowest policies.
- Mod path regressions reproduced before the fix; 100 mod/data-loader tests
  passed, then 46 manager tests passed with the nested-path addition. The symlink
  test skipped because this Windows environment cannot create symlinks.
- Broader affected run completed: **4,554 passed, 195 deselected, 1 failed** in
  26m17s. The failing legacy NPC layout test led to fix 31. Records were not updated.
  This run predates subsequent action/restart/path changes and is not final validation.
- Critical/team-focused checks passed 140 tests. NPC layout/Notes checks passed 93 tests.
- Latest core check: **1,363 passed, 1 skipped** (Windows symlink creation unavailable).
  Affected-test dry selection for the Block data change named all core plus the
  three UI files already included in the 131-test run.
- Startup/packaging: 7 tests passed; headless server and relay `--help` both ran.
- Block/Slam/core/player/GM checks: 131 passed. Malformed-character/snapshot/
  restore subset: 21 passed.
- An earlier plain full run was deliberately stopped after later source/data fixes
  made it a mixed-state run. It is superseded by the frozen final run above.

## Rules interpretation

The rules agent verified that Damage rank zero is legitimate and Accurate Attack
has a rank floor of zero (4e PDF pp54, 118, 202; printed pp52, 116, 200). Applying
maneuver tradeoffs to every Linked component and capping by the lowest component
is the app's interpretation; Linked's shared check does not explicitly settle that
allocation. The fixed defect was inconsistent application of this policy at zero.

## Additional review evidence

- Static dependency check: all 78 core Python modules have no direct PySide6 or
  `mm_companion.ui` imports; no Python files exist under game data.
- Driver-rendered and visually inspected: launcher, character sheet, Power
  Constructor, GM window, NPC sheet, simple sheet, and roller with a maneuver lane.
  All rendered without blank/error frames. Frame checks complement the interaction and failure-path regressions above;
  they do not certify the separate comprehensive table-play checklist.



## Retained policies and feature debts

The action-by-action book/source pass covered the maneuver grid, self/targeted
checks, areas, teams/groups, reroll and request/routine controls. Fresh numerical
and settlement defects are fixed above. Explicit decisions such as free area use,
independent Impress skill/effect selection, next-attack opposing conditions, and
the group/Linked interpretations remain as documented.

The user chose to leave existing timing/automation debts as feature work: maneuver
persistence, Aim lapse/target tracking, successful Recover's scene allowance,
manual temporary clearing, supported advantage gaps listed in the debt notes,
and narrated outcomes. The review does not certify those as RAW-complete and does
not tick the debt file's separate comprehensive table-play checklist.

---
name: combat-maneuvers
description: Resume the combat-maneuvers / Actions-menu feature for MM-Companion — read docs/plans/combat-maneuvers.md, pick up the next unfinished phase, do one phase of work on feature/combat-maneuvers, then record progress in the plan and commit. Use when the user runs /combat-maneuvers, or says "continue the maneuvers", "next phase of the actions menu", "pick up the combat maneuvers plan".
---

# Resume the combat maneuvers feature

The feature is too big for one session, so it runs **one phase per session** against a
written plan. The plan, `docs/plans/combat-maneuvers.md`, is the memory between
sessions. Nothing else is. Trust it over your own assumptions, and leave it accurate for
the next session.

## 1. Get your bearings (always, before any code)

1. `git status` and `git branch --show-current`.
   - Switch to **`feature/combat-maneuvers`** if you're not on it (`git switch`). If the
     branch doesn't exist, stop and ask the user. Don't recreate it.
   - **A dirty tree** means the last session stopped mid-phase. Read the newest
     progress-log entry and `git diff` before touching anything, and carry on from
     there. Never `reset`/`checkout --` someone's changes away.
   - Never commit on `develop` or `main`, never merge, never open a PR (CLAUDE.md
     conventions).
2. `git log --oneline -10` to see what landed since the log's last entry.
3. **Read `docs/plans/combat-maneuvers.md` in full**, especially §1's decisions (the
   user's calls, which are not to be re-litigated) and the progress log.
4. Pick the work: the first phase in the Status table that isn't `done`. If it's
   `in progress (…)`, the text in brackets and the newest log entry say what's left.
5. Read the `docs/notes/` files §3 lists for that area **before** designing (the notes
   are not loaded automatically, and several record bugs that shaped the code). Read
   the code you're about to change.
6. When the phase leans on a rule (caps, an action's numbers, an outcome), check it with
   the **`rulebook`** skill (grep `reference/core-book/`, cite the page). Never `Read`
   the PDF.

## 2. Do the phase

- Work through the phase's checklist in §4. Keep to that phase: if you spot work that
  belongs to a later phase, note it in the log instead of doing it.
- **If a phase is too big for one session**, finish a coherent chunk that leaves the
  tests green, mark the phase `in progress (<what is left>)`, and stop there. A
  half-wired feature with green tests beats a finished one with red tests.
- The standing rules in `CLAUDE.md` hold everywhere: no rules content in Python (it goes
  in `data/`), theme tokens only, widgets never compute game numbers, `core` has no Qt,
  no content-shaped minimum sizes, never show a parentless widget, catch and log
  failures, settings through `core.storage` accessors.
- **A design question the plan doesn't answer and the user should decide:** ask with
  `AskUserQuestion` (give a recommended option), then record the answer in §1's
  decisions. For small calls with an obvious default, decide, and note it in the log.
- **Protocol:** bump at most once for the whole feature (§1 decision 10). Check
  `core/session/protocol.py` against `develop` first: if this branch has already
  bumped, reuse that version.

## 3. Check it

- Run the **`affected-tests`** skill (`python .claude/skills/affected-tests/affected.py`),
  then `ruff check .` and `black .`.
- Run the full `pytest` (~16 min; run it in the background) where the phase says
  "full run recommended", and always in phase 10.
- For a visible UI change, the **`run-mm-companion`** skill can launch the app and take
  a screenshot to confirm it.
- If a test fails and you can't fix it this session, say so plainly in the log and to
  the user. Don't skip it or mark it xfail to get green.

## 4. Write it down, then commit

Before you finish, **update `docs/plans/combat-maneuvers.md`**:
- the Status table row (`done (YYYY-MM-DD)` or `in progress (<what is left>)`);
- tick the phase's checkboxes that are done (add new ones if the work turned up more);
- a **new entry at the top of §6 Progress log**: date, phase, what was done, what's
  next, gotchas worth knowing (a surprising Qt behaviour, a test that's slow, a
  decision you made and why), and the files that matter;
- update §2/§3 if the design changed in practice, so the plan describes the code as it
  is, not as first imagined.

Also update the `docs/notes/` file for the area you changed (the notes describe how the
code works today; the plan describes the feature's journey).

Then commit on `feature/combat-maneuvers`, with an imperative message naming the phase
(e.g. `Add temporary conditions with amounts (combat maneuvers phase 1)`) and ending with
the attribution lines from the session's system reminder. Put the hash in the log entry.
If you can't amend that commit, a small follow-up commit with just the hash is fine.

## 5. Report to the user

Keep it short, outcome first:
- which phase was done (or how far it got), and whether the tests are green;
- anything the user should look at or decide;
- what the next run of `/combat-maneuvers` will do.

Stop after one phase unless the user asked for more. In phase 10, don't merge: tell the
user it's ready for them to call done, and remind them the hub/relay needs a redeploy
for the protocol bump.

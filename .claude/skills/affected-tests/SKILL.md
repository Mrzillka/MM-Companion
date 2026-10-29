---
name: affected-tests
description: Run only the MM-Companion tests a change can affect, instead of the full ~16-minute suite. Use after changing code or data while working on a feature or fix, whenever you would otherwise run `pytest` to check your work — "run the tests", "check nothing broke", "run the affected tests". Not for the final check before a feature is declared done or merged: that is a full run.
---

# Affected tests

The full suite is ~4,400 tests and takes about 16 minutes on the developer's
machine. Most changes can only affect a small part of it. This skill runs that part.

```bash
python .claude/skills/affected-tests/affected.py            # select and run
python .claude/skills/affected-tests/affected.py --dry      # show what would be picked, run nothing
python .claude/skills/affected-tests/affected.py --full     # run everything and rebuild the records
python .claude/skills/affected-tests/affected.py -- -x      # anything after -- goes to pytest
```

Run it from the repo root with the project's venv active. It needs the dev extras
(`pip install -e ".[dev]"`), which include `pytest-testmon`. Don't pass
`-p no:cacheprovider`: testmon reads an option that plugin defines and crashes
without it (the script refuses the flag).

Typical cost on the developer's machine: nothing changed, ~3s; a change to one
small `core` module, ~20s; one edited item in a game-data file, ~1.5 min (all of
`tests/core` plus the files naming the item); the first run or a conftest change,
a full ~18 min that rebuilds the records.

## How it picks

1. **Python code — pytest-testmon.** `.testmondata` records which lines of `src/`
   and `tests/` every test executed. `pytest --testmon` runs only tests whose
   executed code has changed since it last ran them, plus new and edited tests,
   plus tests that failed last time. It compares against what *it last ran*, not
   against `develop` — so after a green run, the next run picks only what changed
   since. Switching branches is fine; it compares file contents.
2. **Files testmon cannot see** — the script handles those. After every green run
   it fingerprints the JSON game data, `ui/block_sizes.json`, theme presets, icons,
   `docs/sample-mods/`, every `conftest.py`, `tests/qt/` and `pyproject.toml`
   (into `.affected-tests/`, gitignored). Next time:
   - **`conftest.py`, `tests/qt/` or `pyproject.toml` changed → full run.**
   - **A game-data file under `src/mm_companion/data/` changed → all of
     `tests/core`** (~10s) plus the tests below.
   - **Any JSON changed → the tests that quote a changed item's `id` or `name`**
     (tests name effects and modifiers by id, advantages and skills by display
     name), or, for files without ids such as theme presets, a changed key or
     `group.name` token. A name found in more than 12 test files is too generic to
     select by and is listed as ignored.
   - **Any other file → the tests that name it.** A sample mod → the tests that
     mention `sample-mods`.
3. The extras run first (`--testmon-noselect`, which runs them and records them),
   then `--testmon` runs the Python-affected tests.

**No records yet** (fresh clone, deleted `.testmondata`, first use) → full run,
which builds them. Recording costs coverage overhead, so that run is slower than a
plain `pytest`.

## Reading the result

The script prints which non-Python files changed, which extra test paths it added
and why, and each `pytest` command it ran. pytest's own summary ends each run; a
testmon run that reports `deselected` tests is working as intended.

- **Exit 0** — every selected test passed (or nothing needed running). The
  records are updated.
- **Non-zero** — some failed. The data records are *not* updated, so the same
  extras are picked again next time, and testmon reruns its failures. Fix and run
  again.

Tell the user how many tests ran and why, not just "tests pass" — e.g. "212 tests:
testmon picked 180 from the change to `core/rules/costs.py`, 32 more quote the
`close_attack` id changed in `advantages.json`."

## What it can miss — and the rule that covers it

A subset run cannot catch everything:

- a test whose outcome depends on **test order** or on state another test leaves
  behind (a subset runs in a different order);
- a file read at runtime that is **not in the watched set** (a new kind of content
  file — add it to `_watched()`);
- a JSON change that matters to a test which **never quotes the changed id**
  (one that loops over every advantage, say) outside `tests/core`.

So: **affected tests while working; a full run before the feature is done.** Before
telling the user a feature is finished or merging a branch into `develop`, run the
full suite — `pytest` locally, or CI on the branch
(`git push` then `gh workflow run CI --ref <branch>`). If a full run finds a
failure the affected run missed, work out why and, if the cause is general, teach
`affected.py` about it.

---
name: gm-session
description: Resume and continue building MM-Companion's GM mode and online session (GM window, player cards, NPCs, shared roll history, hidden rolls, networking). Use when asked to continue/resume the GM window, GM mode, the session, multiplayer, or the next phase of that work — it picks up from GM_SESSION_PLAN.md where the last session left off.
---

# Continue the GM mode / online session work

This is a **large, multi-session feature**: an online session the GM hosts from
the app and players join over the internet, plus the GM window that drives it
(live player cards, NPCs, a synchronised roll history, hidden GM rolls). It is
built one phase at a time, tracked in **`GM_SESSION_PLAN.md`** in the repo root.

**Do exactly one phase per session, then stop.** That file is the source of
truth for what is done and what comes next — read it before anything else.

## Steps

### 1. Read the plan

```bash
cat GM_SESSION_PLAN.md
```

It carries the locked-in design decisions, the architecture, a `Status:` line per
phase, and a progress log. If the file is missing, the feature branch was already
merged and deleted — say so and ask the user what they want instead of guessing.

### 2. Get on the branch

All of this work lives on **one** branch:

```bash
git checkout feature/gm-session
```

If it does not exist, create it off `develop` (`git checkout develop && git
checkout -b feature/gm-session`). **Never** commit on `develop` or `main`, and do
not open a pull request — see `CLAUDE.md`.

### 3. Pick the phase

Take the **first** phase whose `Status:` is not `done`. Announce which one you
are doing. Do not start the one after it, and do not skip ahead because a later
phase looks easier — the phases are ordered by dependency.

If the user names a specific phase, do that one instead.

### 4. Implement it

Follow the architecture in the plan — it names the modules, what belongs in each,
and the existing code to reuse rather than rebuild. Two standing constraints:

- **`ui → core → data`.** Session networking is pure Python in
  `src/mm_companion/core/session/` with no PySide6; Qt lives only in `ui/`.
- **No new dependencies.** PySide6 plus the standard library.

Read `CLAUDE.md` for the wider repo conventions (block registry, signal bus, the
mod pipeline, the OGL boundary on `data/`).

### 5. Verify

Core session code is headless and fast:

```bash
python -m pytest tests/test_session_protocol.py tests/test_session_store.py \
  tests/test_session_server.py tests/test_session_discovery.py
```

(only the files that exist yet). GUI tests need an offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gm_window.py
```

Then always:

```bash
ruff check . && black .
```

Do **not** launch several full `pytest` runs at once — each spawns real Qt
windows that stack up. One known-environmental failure exists locally
(`test_block_sizes`, a font issue on Windows offscreen); it passes on CI and is
not yours to fix.

To see a UI phase working in the real app, use the **`run-mm-companion`** skill.

### 6. Close the phase

Before committing:

1. Flip that phase's `Status:` to `done` in `GM_SESSION_PLAN.md`.
2. Append a dated entry to the **Progress log** at the bottom — what shipped,
   which files, what was deliberately deferred, and anything the next session
   would otherwise have to rediscover. Be concrete; this is the handoff.
3. Commit to `feature/gm-session` in the imperative mood.

Then tell the user what landed and which phase is next. **Do not merge into
`develop`** — that happens only when the user says the whole feature is done, at
which point `GM_SESSION_PLAN.md` is deleted as part of the merge.

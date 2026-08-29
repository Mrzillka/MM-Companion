---
name: make-release
description: Publish a new MM-Companion GitHub Release — write the release description and changelog, commit them with the version bump, then tag and push so CI builds and uploads the shareable Windows installer (.exe) for users to download. Use after build-installer, or whenever asked to publish/cut/ship a release, write release notes or a changelog, push a version tag, or put the installer on GitHub for download.
---

# Publish an MM-Companion release

Turns the current version into a downloadable **GitHub Release** with real notes.
Pushing a `vX.Y.Z` tag fires `.github/workflows/release.yml`, which rebuilds the
installer on a Windows runner, attaches `MM-Companion-Setup-X.Y.Z.exe`, and uses
`docs/releases/vX.Y.Z.md` as the release body — a written description plus a
changelog. The result lands at
`https://github.com/Mrzillka/MM-Companion/releases/latest`.

**Run this *after* `build-installer`.** build-installer bumps `__version__` (and
leaves it *uncommitted*) and builds a local `.exe` as a smoke test. This skill
does not touch the version — it publishes whatever version is committed. The
runner builds its **own** installer from the tagged commit, so the local `.exe`
is never uploaded.

**Every release ships notes.** The repo merges locally and opens no pull
requests, so GitHub's auto-generated notes come out empty — the notes are written
by hand, by you, from the commit log. `make_release.py` refuses to tag without
them.

All paths are relative to the repo root. Two drivers live in
`.claude/skills/make-release/`: `release_notes.py` (material, scaffold, check)
and `make_release.py` (tag + push).

## Mods are released separately

**The installer ships no mods, and this skill publishes none.** Mods live in the
sibling repo (`mm-companion-mods`) and version on their own cadence; that repo has
its own `make-mods-release` skill, which attaches one `.zip` per mod to its own
GitHub Release. A user installs one with **Manage Mods ▸ Add from Zip…**.

The two releases only touch when an app change is what a mod needs: the mods repo
pins the engine in `requirements-app.txt`, so a mod depending on something shipped
here waits for that pin to move. Say so in a mod's release notes, not in this
one's.

## Prerequisites

- The `gh` CLI installed and authenticated (`gh auth status` → logged in). The
  workflow publishes via `gh`, and the driver uses it to locate the run.
- Push access to `origin` (`Mrzillka/MM-Companion`).
- The repo must be **public** for end users to reach the download.

## Steps

### 1. See the level and gather the material

```bash
python .claude/skills/make-release/release_notes.py
```

Read-only. It reports the release **level** and how far back the changelog
reaches, then prints one line per merged feature branch with that branch's own
commits underneath — the raw material for the notes.

| level | version effect | changelog covers | description |
| --- | --- | --- | --- |
| **minor** | `0.3.1` → `0.3.2` | since the previous release tag | short — a few sentences |
| **major** | `0.3.1` → `0.4.0` | since the previous **major** tag (`v0.3.0`) | long — every new feature explained, plus **What's next** |

(These are the project's own terms, the same ones `build-installer` uses: a
*minor* bump moves the last digit, a *major* bump moves the second one. A major
release therefore restates everything the intervening minors shipped.)

### 2. Ask the user before writing

Do this **before** drafting, not after:

- **Always** ask about anything in the log you cannot confidently describe in
  user-facing terms, or where you'd be guessing at what a change means for a
  player.
- **For a major release**, ask which features are the *headline* ones (the log
  won't tell you what the user considers the story of this version), and ask what
  the **near-term plans** are for the *What's next* section. There is no roadmap
  file in this repo — **never invent one**. Use `AskUserQuestion`.

### 3. Write `docs/releases/vX.Y.Z.md`

```bash
python .claude/skills/make-release/release_notes.py --scaffold
```

Writes the level-appropriate skeleton (it refuses to clobber an existing file
without `--force`). Replace **every** `TODO` and every `<!-- ... -->` guidance
comment with real prose per *How to write the notes* below, then:

```bash
python .claude/skills/make-release/release_notes.py --check
```

Show the finished notes to the user before committing them.

### 4. Commit the notes and the version bump together

The CI guard reads `__version__` *at the tagged commit*, and the workflow reads
the notes from that same commit, so both must be committed before tagging.
Follow the repo branch convention — **don't commit on `main` directly**:

```bash
NEW=$(python -c "import mm_companion; print(mm_companion.__version__)")
git checkout -b "release/v$NEW"
git add src/mm_companion/__init__.py "docs/releases/v$NEW.md"
# Optionally also bump the README "Status" line to $NEW in the same commit.
git commit -m "Release v$NEW"
git checkout main
git merge --no-ff "release/v$NEW" -m "Merge release/v$NEW into main"
```

If the bump and notes are already committed on `main` (clean tree, `HEAD`
version == working version), skip this step.

### 5. Preview, then publish

```bash
# Preview — prints version, tag, branch, notes verdict, and what it would do:
python .claude/skills/make-release/make_release.py --dry-run

# Publish — create the annotated tag and push branch + tag (fires the build):
python .claude/skills/make-release/make_release.py
```

The driver refuses (touching nothing) if the bump isn't committed, if the tag
already exists (that version is already released — bump again first), if the
notes are missing or unfinished at HEAD, if you're not on `main` (override with
`--allow-branch`), or if `gh` isn't ready.

### 6. Watch the build and report the link

The driver prints a `gh run watch <id>` command. Run it **in the background** and
tell the user when it's done, rather than blocking:

```bash
gh run watch <id> --exit-status; gh release view "v$NEW" --json url --jq .url
```

(Or pass `--watch` to the driver to block on it synchronously — handy when run by
hand, but prefer backgrounding it as the agent.)

The build takes ~3 min (fresh Python + PyInstaller + Inno Setup on the runner).
When it's green, confirm both the asset **and** the notes actually landed:

```bash
gh release view "v$NEW" --json assets \
  --jq '.assets[] | "\(.name)\t\(.size) bytes\t\(.state)"'
gh release view "v$NEW"   # the body should be your notes, not a bare compare link
```

`state=uploaded` and a ~80 MB size means the installer is good.

## How to write the notes

Write for **players downloading the app**, not for developers.

- **Say what was done, not how.** No file names, class names, branch names, or
  commit hashes. "The dice roller is now a block you can pin beside your sheet",
  not "added `DiceSection` to the block registry".
- **Roll up the trivia.** A batch of small fixes is one line — "Fixed a batch of
  GM session bugs", "Visual polish across the sheet". Nobody needs every commit.
- **Name what's valuable.** A bug users actually hit, a behaviour change, anything
  that changes how you work — give it its own line.
- One merged feature branch is usually one bullet (minor) or one `###` block
  (major). Internal-only work (refactors, test isolation, CI) is omitted, or
  folded into a single "under the hood" bullet.
- **Never repeat the release title as a heading** — `--title` already sets
  "MM-Companion X.Y.Z". The body starts with the description and uses `##`.
- Keep the `**Install:**` and `**Full changelog:**` lines the scaffold ends with.

**Minor** body — a description and a flat changelog:

```markdown
<1–3 sentences: what this update is for.>

## Changes

- <named, valuable change or fix>
- <…>
- Various visual polish and smaller fixes.

**Install:** download `MM-Companion-Setup-X.Y.Z.exe` below and run it.

**Full changelog:** https://github.com/Mrzillka/MM-Companion/compare/vPREV...vX.Y.Z
```

**Major** body — the features explained, then everything since the last major:

```markdown
<a paragraph: what this version is and why it matters.>

## What's new

### <Feature name>

<2–4 sentences, player-facing — what you can now do with it.>

### <Feature name>

<…>

## Changes since X.(Y-1).0

- **Added** …
- **Changed** …
- **Fixed** …

## What's next

- <near-term plans — the user's, confirmed in step 2>

**Install:** download `MM-Companion-Setup-X.Y.Z.exe` below and run it.

**Full changelog:** https://github.com/Mrzillka/MM-Companion/compare/vPREVMAJOR...vX.Y.Z
```

## What the drivers do

`release_notes.py` (read-only unless `--scaffold`):

1. Reads `__version__` → the level (a `.0` patch digit means major) and the base
   tag the changelog reaches back to (previous major tag for a major, previous
   release tag for a minor; falls back to the first commit if neither exists).
2. `--material` (the default) prints the merge-level log plus each branch's
   commits, and a size summary.
3. `--scaffold` writes `docs/releases/vX.Y.Z.md` from the level's template
   (`--out` to write elsewhere, `--force` to overwrite).
4. `--check` exits non-zero while the notes are missing, still hold `TODO`
   markers, or are trivially short — the same predicate the release guard uses.

`make_release.py`:

1. Reads `__version__` → tag `vX.Y.Z`.
2. Runs the guards above (all abort cleanly, changing nothing).
3. `git tag -a vX.Y.Z -m "MM-Companion X.Y.Z"`, then pushes the branch and the
   tag to the remote. The tag push triggers `release.yml`.
4. Prints the release URL and the `gh run watch` command; `--watch` blocks until
   the run finishes and prints the published release URL.

## Gotchas

- **The notes must be committed, not just written.** The guard reads
  `HEAD:docs/releases/vX.Y.Z.md`, because CI reads the file out of the *tagged
  commit*. A finished file sitting in the working tree still refuses.
- **CI won't catch missing notes** — if a tag has no notes file, the workflow logs
  a warning and falls back to auto-generated (i.e. empty) notes rather than
  failing the build. The `make_release.py` guard is what actually enforces this;
  `--no-notes` deliberately escapes it.
- **The tag must match the committed version** — the driver derives the tag from
  the committed `__version__`, so this is automatic *as long as the bump is
  committed*. That's why step 4 matters.
- **Re-releasing the same version is blocked.** Tags are immutable public refs;
  to ship again, bump the version (build-installer) and tag the new one. Don't
  force-move a released tag.
- **Notes can still be fixed after publishing** — the body is not immutable:
  `gh release edit vX.Y.Z --notes-file docs/releases/vX.Y.Z.md` re-uploads it
  (commit the corrected file too, so the repo and the release agree).
- **A manual (non-tag) build** is available from the Actions tab
  (`workflow_dispatch`) — it keeps the `.exe` as a temporary build artifact
  instead of publishing a release. Use it to smoke-test CI without shipping.
- **First digit (`X`) / `1.0.0`** — build-installer only does `minor`/`major`
  bumps; a `1.0.0` is a manual version edit, then this skill from step 1 (it
  counts as a major release: the changelog reaches back to the last `X.Y.0`).
- **`installer/output/*.exe` is git-ignored** — never commit the local build;
  the release asset comes from the runner.

## Troubleshooting

- *"Release notes are not ready: …"* — do steps 1–4: `--scaffold`, write the
  prose, `--check`, and commit `docs/releases/vX.Y.Z.md` with the bump.
- *"Version bump is not committed"* — do step 4 (commit the bump to `main`).
- *"Tag vX.Y.Z already exists"* — that version is already released; run
  build-installer to bump first.
- *"On branch '…', not 'main'"* — switch to `main` (the release branch) or pass
  `--allow-branch` if you deliberately mean to tag elsewhere.
- *"gh is missing or not authenticated"* — install the GitHub CLI and
  `gh auth login`.
- **Workflow fails at "Verify tag matches package version"** — the tag and
  committed `__version__` disagree; you tagged the wrong commit. Delete the bad
  tag (`git push origin :refs/tags/vX.Y.Z` and `git tag -d vX.Y.Z`), fix the
  commit, and re-run.
- **The published release shows a bare compare link** — the notes file wasn't in
  the tagged commit. Fix it with `gh release edit` (see Gotchas), then work out
  how the guard came to be bypassed.

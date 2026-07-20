---
name: make-release
description: Publish a new MM-Companion GitHub Release — commit the version bump, then tag and push so CI builds and uploads the shareable Windows installer (.exe) for users to download. Use after build-installer, or whenever asked to publish/cut/ship a release, push a version tag, or put the installer on GitHub for download.
---

# Publish an MM-Companion release

Turns the current version into a downloadable **GitHub Release**. Pushing a
`vX.Y.Z` tag fires `.github/workflows/release.yml`, which rebuilds the installer
on a Windows runner and attaches `MM-Companion-Setup-X.Y.Z.exe` to a new release
at `https://github.com/Mrzillka/MM-Companion/releases/latest`.

**Run this *after* `build-installer`.** build-installer bumps `__version__` (and
leaves it *uncommitted*) and builds a local `.exe` as a smoke test. This skill
does not touch the version — it publishes whatever version is committed. The
runner builds its **own** installer from the tagged commit, so the local `.exe`
is never uploaded.

All paths are relative to the repo root. The driver is
`.claude/skills/make-release/make_release.py`.

## Prerequisites

- The `gh` CLI installed and authenticated (`gh auth status` → logged in). The
  workflow publishes via `gh`, and the driver uses it to locate the run.
- Push access to `origin` (`Mrzillka/MM-Companion`).
- The repo must be **public** for end users to reach the download.

## Steps

### 1. Commit the version bump (agent does this — the driver refuses without it)

build-installer leaves `src/mm_companion/__init__.py` bumped but uncommitted. The
CI guard reads `__version__` *at the tagged commit* and fails a mismatch, so the
bump must be committed to `main` before tagging. Follow the repo branch
convention — **don't commit on `main` directly**:

```bash
NEW=$(python -c "import mm_companion; print(mm_companion.__version__)")
git checkout -b "release/v$NEW"
git add src/mm_companion/__init__.py
# Optionally also bump the README "Status" line to $NEW in the same commit.
git commit -m "Release v$NEW"
git checkout main
git merge --no-ff "release/v$NEW" -m "Merge release/v$NEW into main"
```

If the bump is already committed on `main` (clean tree, `HEAD` version ==
working version), skip this step.

### 2. Preview, then publish

```bash
# Preview — prints version, tag, branch, and what it would do. Changes nothing:
python .claude/skills/make-release/make_release.py --dry-run

# Publish — create the annotated tag and push branch + tag (fires the build):
python .claude/skills/make-release/make_release.py
```

The driver refuses (touching nothing) if the bump isn't committed, if the tag
already exists (that version is already released — bump again first), if you're
not on `main` (override with `--allow-branch`), or if `gh` isn't ready.

### 3. Watch the build and report the link

The driver prints a `gh run watch <id>` command. Run it **in the background** and
tell the user when it's done, rather than blocking:

```bash
gh run watch <id> --exit-status; gh release view "v$NEW" --json url --jq .url
```

(Or pass `--watch` to the driver to block on it synchronously — handy when run by
hand, but prefer backgrounding it as the agent.)

The build takes ~3 min (fresh Python + PyInstaller + Inno Setup on the runner).
When it's green, the installer is downloadable at
`https://github.com/Mrzillka/MM-Companion/releases/latest`. To confirm the asset
is intact:

```bash
gh release view "v$NEW" --json assets \
  --jq '.assets[] | "\(.name)\t\(.size) bytes\t\(.state)"'
```

`state=uploaded` and a ~80 MB size means it's good.

## What the driver does

1. Reads `__version__` → tag `vX.Y.Z`.
2. Runs the guards above (all abort cleanly, changing nothing).
3. `git tag -a vX.Y.Z -m "MM-Companion X.Y.Z"`, then pushes the branch and the
   tag to the remote. The tag push triggers `release.yml`.
4. Prints the release URL and the `gh run watch` command; `--watch` blocks until
   the run finishes and prints the published release URL.

## Gotchas

- **The tag must match the committed version** — the driver derives the tag from
  the committed `__version__`, so this is automatic *as long as the bump is
  committed*. That's why step 1 matters.
- **Re-releasing the same version is blocked.** Tags are immutable public refs;
  to ship again, bump the version (build-installer) and tag the new one. Don't
  force-move a released tag.
- **A manual (non-tag) build** is available from the Actions tab
  (`workflow_dispatch`) — it keeps the `.exe` as a temporary build artifact
  instead of publishing a release. Use it to smoke-test CI without shipping.
- **First digit (`X`) / `1.0.0`** — build-installer only does `minor`/`major`
  bumps; a `1.0.0` is a manual version edit, then this skill from step 1.
- **`installer/output/*.exe` is git-ignored** — never commit the local build;
  the release asset comes from the runner.

## Troubleshooting

- *"Version bump is not committed"* — do step 1 (commit the bump to `main`).
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

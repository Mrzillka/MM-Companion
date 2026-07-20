#!/usr/bin/env python3
"""Tag the committed version and push it to trigger the GitHub Release build.

Run this AFTER ``build-installer`` has bumped the version and that bump has been
committed to ``main``. It is the mechanical, error-prone half of cutting a
release, done safely and idempotently:

  1. Reads ``__version__`` from ``src/mm_companion/__init__.py``  ->  tag ``vX.Y.Z``.
  2. Refuses (with a clear reason, changing nothing) if the bump is not committed,
     if that tag already exists, if you are not on the release branch, or if the
     ``gh`` CLI is missing / not authenticated — so a release is never
     half-made, duplicated, or built from the wrong commit.
  3. Creates an annotated ``vX.Y.Z`` tag and pushes the branch + tag to origin.

Pushing the ``v*`` tag fires ``.github/workflows/release.yml``, which rebuilds
the installer on a Windows runner and publishes it as a GitHub Release asset
(``MM-Companion-Setup-X.Y.Z.exe``). That workflow re-verifies the tag matches
the committed package version; this script guarantees the match by construction
(it derives the tag *from* the committed version), so the guard never trips.

Usage (from anywhere in the repo)::

    python .claude/skills/make-release/make_release.py --dry-run   # preview only
    python .claude/skills/make-release/make_release.py             # tag + push
    python .claude/skills/make-release/make_release.py --watch      # + wait for CI

The local ``.exe`` from ``build-installer`` is NOT uploaded — the runner builds
its own from the tagged commit. build-installer's local build is a smoke test.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION_RE = re.compile(r'(?m)^__version__\s*=\s*"(\d+\.\d+\.\d+)"')
RELEASE_BRANCH = "main"
INIT_REL = "src/mm_companion/__init__.py"


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    raise SystemExit(f"!! {msg}")


def find_repo_root(start: Path) -> Path:
    for d in (start, *start.parents):
        if (d / "pyproject.toml").exists() and (d / "src" / "mm_companion").is_dir():
            return d
    fail("Could not locate the repo root (pyproject.toml + src/mm_companion).")


def run(cmd: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), check=check, text=True, capture_output=True)


def git(root: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], root, check=check).stdout.strip()


def parse_version(text: str, where: str) -> str:
    m = VERSION_RE.search(text)
    if not m:
        fail(f'No 3-part __version__ = "X.Y.Z" found in {where}.')
    return m.group(1)


def tag_exists(root: Path, tag: str, remote: str) -> bool:
    if git(root, "tag", "--list", tag):
        return True
    ls = git(root, "ls-remote", "--tags", remote, f"refs/tags/{tag}", check=False)
    return bool(ls)


def gh_ready() -> bool:
    try:
        return run(["gh", "auth", "status"], Path.cwd(), check=False).returncode == 0
    except FileNotFoundError:
        return False


def latest_release_run(root: Path) -> str | None:
    """Return the databaseId of the most recent release.yml run, if gh can see it."""
    res = run(
        ["gh", "run", "list", "--workflow=release.yml", "--limit", "1", "--json", "databaseId"],
        root,
        check=False,
    )
    if res.returncode != 0:
        return None
    import json

    try:
        rows = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return str(rows[0]["databaseId"]) if rows else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tag the committed version and push it to publish a GitHub Release."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen (version, tag, branch) and change nothing.",
    )
    parser.add_argument("--remote", default="origin", help="Git remote to push to (default: origin).")
    parser.add_argument(
        "--allow-branch",
        action="store_true",
        help=f"Skip the guard that requires being on '{RELEASE_BRANCH}'.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="After pushing, block until the release workflow finishes (~3 min).",
    )
    args = parser.parse_args()

    root = find_repo_root(Path(__file__).resolve())
    init_file = root / INIT_REL

    work_version = parse_version(init_file.read_text(encoding="utf-8"), str(init_file))
    tag = f"v{work_version}"
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")

    # --- Preconditions (each aborts without touching anything) ---

    # 1. The bump must be committed: the tag will point at HEAD, and the CI
    #    guard reads __version__ *at the tagged commit*.
    head_init = git(root, "show", f"HEAD:{INIT_REL}", check=False)
    head_version = parse_version(head_init, f"HEAD:{INIT_REL}") if head_init else "<none>"
    bump_committed = head_version == work_version

    # 2. Not already released.
    already_tagged = tag_exists(root, tag, args.remote)

    # 3. On the release branch (unless overridden).
    wrong_branch = branch != RELEASE_BRANCH and not args.allow_branch

    print(f"  version (working): {work_version}")
    print(f"  version (HEAD):    {head_version}")
    print(f"  tag:               {tag}")
    print(f"  branch:            {branch}")
    print(f"  remote:            {args.remote}")

    if args.dry_run:
        print("\n[dry-run] would:")
        if not bump_committed:
            print("  - REFUSE: version bump is not committed (commit it first).")
        elif already_tagged:
            print(f"  - REFUSE: tag {tag} already exists (bump the version to release again).")
        elif wrong_branch:
            print(f"  - REFUSE: on '{branch}', not '{RELEASE_BRANCH}' (use --allow-branch to override).")
        else:
            print(f"  - create annotated tag {tag} at {git(root, 'rev-parse', '--short', 'HEAD')}")
            print(f"  - push {branch} and {tag} to {args.remote} (fires release.yml)")
        print("  (no files or refs changed)")
        return 0

    if not bump_committed:
        fail(
            f"Version bump is not committed: HEAD has {head_version}, working tree has "
            f"{work_version}. Commit the bump to '{RELEASE_BRANCH}' first, then re-run."
        )
    if already_tagged:
        fail(
            f"Tag {tag} already exists locally or on {args.remote}; {work_version} is already "
            "released. Run build-installer to bump the version before releasing again."
        )
    if wrong_branch:
        fail(
            f"On branch '{branch}', not '{RELEASE_BRANCH}'. Releases are tagged on "
            f"'{RELEASE_BRANCH}'. Switch to it (or pass --allow-branch if you mean to)."
        )
    if not gh_ready():
        fail(
            "The GitHub CLI (gh) is missing or not authenticated. Install it and run "
            "'gh auth login' — the release workflow publishes via gh, and this script "
            "uses it to find the run."
        )

    # --- Do it ---
    print(f"\n==> tagging {tag} and pushing to {args.remote}")
    git(root, "tag", "-a", tag, "-m", f"MM-Companion {work_version}")
    # Push the branch first (keeps the public branch consistent), then the tag
    # that actually triggers the release build.
    print(run(["git", "push", args.remote, branch], root).stderr.strip())
    print(run(["git", "push", args.remote, tag], root).stderr.strip())

    print("\n" + "=" * 60)
    print(f"  Pushed {tag}. release.yml is now building + publishing the installer.")
    print(f"  Release page (live in ~3 min):")
    print("    https://github.com/Mrzillka/MM-Companion/releases/latest")

    # Give the run a moment to register, then point at it.
    time.sleep(4)
    run_id = latest_release_run(root)
    if run_id:
        print(f"  Watch the build:  gh run watch {run_id} --exit-status")
    print("=" * 60)

    if args.watch and run_id:
        print("\nWatching the release build...")
        rc = subprocess.run(
            ["gh", "run", "watch", run_id, "--exit-status"], cwd=str(root)
        ).returncode
        if rc != 0:
            fail(f"Release workflow run {run_id} did not succeed (exit {rc}). See the Actions tab.")
        url = run(
            ["gh", "release", "view", tag, "--json", "url", "--jq", ".url"], root, check=False
        ).stdout.strip()
        print(f"\n✓ Release published: {url or 'see the Releases page'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Gather the material for a release's notes, and scaffold the notes file.

Every MM-Companion release ships a written description and a changelog, kept in
``docs/releases/vX.Y.Z.md``. That file is committed alongside the version bump,
and ``.github/workflows/release.yml`` publishes it as the GitHub Release body
(falling back to auto-generated notes only when the file is absent). This script
is the half of that a machine can do:

  1. Works out the *level* of the release from the version alone — a ``.0`` patch
     digit is this project's "major" bump (``0.3.1 -> 0.4.0``), anything else is a
     "minor" one (``0.3.1 -> 0.3.2``) — and therefore how far back the changelog
     reaches: a minor covers the previous release tag, a major covers the previous
     *major* tag, so it restates everything the intervening minors shipped.
  2. Prints the raw ingredients (``--material``): the merge-level log, which is
     one line per feature branch and the natural granularity of a changelog, with
     each branch's own commits underneath for detail.
  3. Writes a level-appropriate skeleton (``--scaffold``) full of TODO markers.
  4. Answers whether the notes are actually finished (``--check``) — the same
     predicate ``make_release.py`` guards a release with.

The prose itself is not something this script can write: turning "Merge
feature/pinned-block-strip into develop" into a sentence a player understands is
the agent's job. See the make-release skill for how to write it.

Usage (from anywhere in the repo)::

    python .claude/skills/make-release/release_notes.py             # material
    python .claude/skills/make-release/release_notes.py --scaffold  # write skeleton
    python .claude/skills/make-release/release_notes.py --check     # finished?
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import NoReturn

VERSION_RE = re.compile(r'(?m)^__version__\s*=\s*"(\d+\.\d+\.\d+)"')
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
INIT_REL = "src/mm_companion/__init__.py"
NOTES_DIR = "docs/releases"
REPO_URL = "https://github.com/Mrzillka/MM-Companion"
TODO_MARK = "TODO"
# A finished set of notes is at least a description and a couple of bullets; the
# guard only means to catch an untouched or half-written file, not to grade prose.
MIN_NOTES_CHARS = 200


def fail(msg: str) -> NoReturn:
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


def version_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(p) for p in version.split("."))
    return major, minor, patch


def release_level(version: str) -> str:
    """The project's own bump level: X.Y.0 is "major", any other patch "minor"."""
    return "major" if version_tuple(version)[2] == 0 else "minor"


def release_tags(root: Path) -> list[tuple[tuple[int, int, int], str]]:
    """Every vX.Y.Z tag in the repo, ascending by version (not by date)."""
    tags = []
    for line in git(root, "tag", "--list", "v*").splitlines():
        m = TAG_RE.match(line.strip())
        if m:
            tags.append(((int(m[1]), int(m[2]), int(m[3])), line.strip()))
    return sorted(tags)


def previous_tag(root: Path, version: str) -> str | None:
    """The highest released tag below `version`."""
    here = version_tuple(version)
    earlier = [tag for key, tag in release_tags(root) if key < here]
    return earlier[-1] if earlier else None


def previous_major_tag(root: Path, version: str) -> str | None:
    """The highest released X.Y.0 tag below `version`, else the earliest tag."""
    here = version_tuple(version)
    tags = release_tags(root)
    majors = [tag for key, tag in tags if key < here and key[2] == 0]
    if majors:
        return majors[-1]
    earlier = [tag for key, tag in tags if key < here]
    return earlier[0] if earlier else None


def base_ref(root: Path, version: str) -> tuple[str, str]:
    """Return (ref, label) the changelog reaches back to for this version's level."""
    tag = (
        previous_major_tag(root, version)
        if release_level(version) == "major"
        else previous_tag(root, version)
    )
    if tag:
        return tag, tag
    first = git(root, "rev-list", "--max-parents=0", "HEAD").split()
    if not first:
        fail("The repo has no commits to compare against.")
    return first[0], "the first commit (no earlier release tag exists)"


def covered_releases(root: Path, base: str, version: str) -> list[str]:
    """Released versions between `base` and this one — restated by a major's log."""
    here = version_tuple(version)
    low = version_tuple(base[1:]) if TAG_RE.match(base) else (0, 0, 0)
    return [tag for key, tag in release_tags(root) if low < key < here]


def merge_material(root: Path, base: str) -> list[str]:
    """The merge-level log, each merge followed by the commits it brought in."""
    lines: list[str] = []
    merges = git(root, "log", "--first-parent", "--format=%H\t%h\t%s", f"{base}..HEAD")
    if not merges:
        return ["  (no commits since the base — nothing to write about)"]
    for row in merges.splitlines():
        full, short, subject = row.split("\t", 2)
        lines.append(f"  {short}  {subject}")
        # ^1..^2 is a merge's own branch; a plain commit has no ^2 and is skipped.
        branch = git(root, "log", "--format=%s", f"{full}^1..{full}^2", check=False)
        for commit in branch.splitlines():
            lines.append(f"        - {commit}")
    return lines


def notes_path(root: Path, version: str) -> Path:
    return root / NOTES_DIR / f"v{version}.md"


def notes_are_finished(text: str) -> tuple[bool, str]:
    """Whether `text` reads as written-out notes, plus the reason when it doesn't."""
    if not text.strip():
        return False, "the notes file is empty"
    if TODO_MARK in text:
        return False, "the notes still contain scaffold TODO markers"
    if len(text.strip()) < MIN_NOTES_CHARS:
        return False, f"the notes are only {len(text.strip())} characters — looks unfinished"
    return True, "ok"


def compare_url(base: str, version: str) -> str:
    return f"{REPO_URL}/compare/{base}...v{version}"


MINOR_TEMPLATE = """\
<!-- TODO: 1-3 sentences for players: what this update is for. -->

## Changes

<!-- TODO: one bullet per thing worth knowing. Name the valuable fixes and
     changes; roll the rest up ("Various visual polish and smaller fixes").
     Say what was done, not how — no file, class, or branch names. -->
- TODO

**Install:** download `MM-Companion-Setup-{version}.exe` below and run it.

**Full changelog:** {compare}
"""

MAJOR_TEMPLATE = """\
<!-- TODO: a paragraph for players: what this version is and why it matters. -->

## What's new

<!-- TODO: one ### block per headline feature, 2-4 sentences each, describing
     what you can now DO with it. Confirm the headline list with the user. -->

### TODO

TODO

## Changes since {base_version}

<!-- TODO: the full changelog since the previous major release{covered_note}.
     Group as Added / Changed / Fixed. Name what matters, roll up the rest. -->
- **Added** TODO
- **Changed** TODO
- **Fixed** TODO

## What's next

<!-- TODO: near-term plans. ASK THE USER — never invent a roadmap. -->
- TODO

**Install:** download `MM-Companion-Setup-{version}.exe` below and run it.

**Full changelog:** {compare}
"""


def scaffold_text(root: Path, version: str, base: str) -> str:
    compare = compare_url(base, version)
    if release_level(version) == "minor":
        return MINOR_TEMPLATE.format(version=version, compare=compare)
    covered = covered_releases(root, base, version)
    covered_note = f", which also restates {', '.join(covered)}" if covered else ""
    base_version = base[1:] if TAG_RE.match(base) else "the first release"
    return MAJOR_TEMPLATE.format(
        version=version,
        compare=compare,
        base_version=base_version,
        covered_note=covered_note,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gather material for, scaffold, and check a release's notes."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--material",
        action="store_true",
        help="Print the commit material to write the notes from (the default).",
    )
    mode.add_argument(
        "--scaffold",
        action="store_true",
        help="Write a level-appropriate skeleton to docs/releases/vX.Y.Z.md.",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero unless the notes exist and are written out.",
    )
    parser.add_argument("--out", help="Write the scaffold here instead of docs/releases/vX.Y.Z.md.")
    parser.add_argument(
        "--force", action="store_true", help="Let --scaffold overwrite existing notes."
    )
    args = parser.parse_args()

    root = find_repo_root(Path(__file__).resolve())
    version = parse_version((root / INIT_REL).read_text(encoding="utf-8"), INIT_REL)
    level = release_level(version)
    base, base_label = base_ref(root, version)
    path = Path(args.out) if args.out else notes_path(root, version)

    if args.check:
        if not path.exists():
            fail(f"No release notes at {path.relative_to(root) if not args.out else path}.")
        ok, why = notes_are_finished(path.read_text(encoding="utf-8"))
        if not ok:
            fail(f"Release notes are not ready: {why}.")
        print(f"OK: {path} reads as finished notes.")
        return 0

    if args.scaffold:
        if path.exists() and not args.force:
            fail(f"{path} already exists (pass --force to overwrite).")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(scaffold_text(root, version, base), encoding="utf-8")
        print(f"  wrote {level} scaffold: {path}")
        print("  Replace every TODO with real prose, then re-run with --check.")
        return 0

    # --- Default: the material to write from ---
    covered = covered_releases(root, base, version)
    print(f"  version:      {version}")
    print(f"  level:        {level} ({'X.Y.0 bump' if level == 'major' else 'last-digit bump'})")
    print(f"  changelog to: {base_label}")
    if covered:
        print(f"  restates:     {', '.join(covered)}")
    print(f"  notes file:   {notes_path(root, version).relative_to(root).as_posix()}")
    print(f"  compare URL:  {compare_url(base, version)}")

    print(f"\n=== merged branches since {base} (one line per feature) ===")
    for line in merge_material(root, base):
        print(line)

    stat = git(root, "diff", "--shortstat", f"{base}..HEAD", check=False)
    if stat:
        print(f"\n=== size ===\n  {stat}")

    print("\nNext: --scaffold, write the prose (see the make-release skill), then --check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

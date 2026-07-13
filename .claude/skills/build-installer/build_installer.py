#!/usr/bin/env python3
"""Bump the app version and build a fresh MM-Companion Windows installer.

The single source of truth for the version is ``__version__`` in
``src/mm_companion/__init__.py`` (``pyproject.toml`` derives its version from it,
and ``installer/build.ps1`` reads it to name the installer). This script bumps
that string, then runs the installer build, producing::

    installer/output/MM-Companion-Setup-<new-version>.exe

Version bump levels (versions are 3-part SemVer ``X.Y.Z``):

    minor  -> +1 to the LAST digit   (0.1.2 -> 0.1.3)   # patch position
    major  -> +1 to the SECOND digit (0.1.2 -> 0.2.0)   # resets the last to 0

Usage (run with the project's venv Python so the freeze uses the right deps)::

    python .claude/skills/build-installer/build_installer.py minor
    python .claude/skills/build-installer/build_installer.py major
    python .claude/skills/build-installer/build_installer.py minor --dry-run

On a build failure the version file is restored, so a failed build never leaves
the repo half-bumped. A successful build leaves the new version in place (commit
it if you want to keep the release); it is NOT auto-committed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r'(?m)^(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")')


def find_repo_root(start: Path) -> Path:
    """Walk up from *start* to the repo root (has pyproject.toml + the package)."""
    for d in (start, *start.parents):
        if (d / "pyproject.toml").exists() and (d / "src" / "mm_companion").is_dir():
            return d
    raise SystemExit("Could not locate the repo root (pyproject.toml + src/mm_companion).")


def read_version(init_file: Path) -> tuple[str, tuple[int, int, int]]:
    """Return the raw ``X.Y.Z`` string and its parsed parts from __init__.py."""
    text = init_file.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        raise SystemExit(f"No 3-part __version__ = \"X.Y.Z\" found in {init_file}.")
    major, minor, patch = int(m.group(2)), int(m.group(3)), int(m.group(4))
    return f"{major}.{minor}.{patch}", (major, minor, patch)


def bump(parts: tuple[int, int, int], level: str) -> str:
    """Apply the requested bump and return the new ``X.Y.Z`` string."""
    major, minor, patch = parts
    if level == "minor":  # last digit
        return f"{major}.{minor}.{patch + 1}"
    if level == "major":  # second digit, reset the last
        return f"{major}.{minor + 1}.0"
    raise SystemExit(f"Unknown level {level!r} (expected 'minor' or 'major').")


def write_version(init_file: Path, new_version: str) -> None:
    text = init_file.read_text(encoding="utf-8")
    new_text = VERSION_RE.sub(lambda m: f"{m.group(1)}{new_version}{m.group(5)}", text, count=1)
    init_file.write_text(new_text, encoding="utf-8")


def run_build(repo_root: Path) -> None:
    """Invoke installer/build.ps1 with this same Python interpreter."""
    build_ps1 = repo_root / "installer" / "build.ps1"
    # Prefer PowerShell 7 (pwsh) but fall back to Windows PowerShell.
    shell = "pwsh"
    try:
        subprocess.run([shell, "-NoLogo", "-Command", "$PSVersionTable.PSVersion"],
                       check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        shell = "powershell"
    subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(build_ps1),
         "-PythonExe", sys.executable],
        cwd=str(repo_root),
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump the version and build a new installer.")
    parser.add_argument("level", choices=["minor", "major"],
                        help="minor = +1 last digit (0.1.2->0.1.3); major = +1 second digit (0.1.2->0.2.0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the version change without editing files or building.")
    args = parser.parse_args()

    repo_root = find_repo_root(Path(__file__).resolve())
    init_file = repo_root / "src" / "mm_companion" / "__init__.py"

    old_version, parts = read_version(init_file)
    new_version = bump(parts, args.level)
    label = args.level.upper()

    if args.dry_run:
        print(f"[dry-run] {label} update: {old_version} -> {new_version} (no files changed)")
        return 0

    original_text = init_file.read_text(encoding="utf-8")
    write_version(init_file, new_version)
    print(f"==> {label} update: {old_version} -> {new_version}")

    try:
        run_build(repo_root)
    except subprocess.CalledProcessError as exc:
        init_file.write_text(original_text, encoding="utf-8")  # roll back the bump
        print(f"\n!! Build failed (exit {exc.returncode}); version restored to {old_version}.",
              file=sys.stderr)
        return exc.returncode

    installer = repo_root / "installer" / "output" / f"MM-Companion-Setup-{new_version}.exe"
    if not installer.exists():
        print(f"\n!! Build finished but {installer} is missing.", file=sys.stderr)
        return 1

    size_mb = installer.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"  {label} update complete: {old_version} -> {new_version}")
    print(f"  Installer: {installer}")
    print(f"  Size: {size_mb:.1f} MB")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
